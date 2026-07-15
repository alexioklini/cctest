"""M5 (Auto-Release) + M10b (Ad-hoc-Schutz ohne Projekt) — Egress-Gate.

M5/G3 — die Komposition tötete den Use-Case: der Gate liess `organisation`
beim Frisch-Scan durch (die Policy stuft eine juristische Person nicht als
personenbezogenes Datum ein), aber sobald M4 den Firmennamen FAKT, kannte das
Modell nur noch den Fake — und Fakes refusten in JEDEM Modus. Jede
Einzelkomponente war korrekt; zusammen machten sie Firmen-Recherche UNMÖGLICH,
obwohl Adverse-Media-/Sanktions-/Registry-Screening der Zweck der betroffenen
Projekte IST.

Auto-Release: trägt ein Fake einen Wert, dessen KATEGORIE die Policy ohnehin
passieren lässt (business_id, network), wird er für den AUSGEHENDEN Request
hin-übersetzt statt refused. Das Modell sieht das Original nie (die Übersetzung
lebt nur in der Dispatch-Kopie der Args), die Suchmaschine bekommt den Namen,
den die Policy ihr ohnehin zugesteht.

DIE NICHT-VERHANDELBARE INVARIANTE: PERSONEN-Fakes refusen weiter — in JEDEM
Modus, auch `allow`. Eine Fake-Personensuche ist semantisch leer oder trifft
echte FREMDE Personen (Fake-Namen sind reale Namen) → Gift-Evidenz.

M10b/G13 — der Schutz hing am Projekt-Preset, die Arbeit tat das nicht: die
MEHRHEIT der realen KYC-/DD-Chats lief PROJEKTLOS (587a737dc21d, 1a830369e762,
088683fc47bc). Kein Preset → kein Mapping → Gate KOMPLETT AUS → der Klarname
ging in Turn 1 an die Suchmaschine. Jetzt greift der Frisch-Scan des Gates auch
ohne Mapping, sobald der Scanner aktiv ist.

Run: python3 -m unittest tests.test_web_auto_release -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import pseudonymizer as ps  # noqa: E402
from engine.context import request_context, get_request_context  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        self._orig_cfg_fn = brain._get_gdpr_scanner_config
        self._mappings = []
        from engine import pii_ner as _pn
        self._saved_nlp = dict(_pn._NLP_CACHE)
        _pn._NLP_CACHE.clear()   # NER aus (Ordnungs-Flake, v9.342.0)

    def tearDown(self):
        brain._get_gdpr_scanner_config = self._orig_cfg_fn
        for m in self._mappings:
            ps.close_mapping(m.mapping_id)
        from engine import pii_ner as _pn
        _pn._NLP_CACHE.clear()
        _pn._NLP_CACHE.update(self._saved_nlp)

    def _mapping(self):
        m = ps.new_mapping()
        self._mappings.append(m)
        return m

    def _cfg(self, mode, *, enabled=True):
        cfg = dict(brain._get_gdpr_scanner_config())
        cfg["enabled"] = enabled
        cfg["web_egress"] = mode
        cfg["rule_overrides"] = {**(cfg.get("rule_overrides") or {}),
                                 "organisation": "warn", "name": "warn"}
        brain._get_gdpr_scanner_config = lambda: cfg
        return cfg

    def _guard(self, args, mapping=None, tool="searxng_search"):
        with request_context():
            ctx = get_request_context()
            ctx._gdpr_mapping_id = mapping.mapping_id if mapping else ""
            ctx.current_session_id = None
            return brain._gdpr_guard_web_args(tool, args)


_MODES = ("refuse", "ask", "allow", "block_group")


class TestOrgAutoRelease(_Base):
    """Der Firmen-Fake wird hin-übersetzt statt refused — in JEDEM Modus."""

    def _org_mapping(self):
        m = self._mapping()
        ps._entity_fake_organisation("Wiener Privatbank SE", m)
        return m, m.forward["Wiener Privatbank"]

    def test_org_fake_released_in_every_mode(self):
        for mode in _MODES:
            with self.subTest(mode=mode):
                self._cfg(mode)
                m, org_fake = self._org_mapping()
                ref, args = self._guard({"query": f"{org_fake} sanctions"}, m)
                self.assertIsNone(ref, f"{mode}: Firmen-Recherche muss laufen")
                self.assertIn("Wiener Privatbank", args["query"],
                              f"{mode}: der ECHTE Firmenname muss an die "
                              f"Suchmaschine gehen (Auto-Release)")
                self.assertNotIn(org_fake, args["query"],
                                 f"{mode}: der Fake darf nicht rausgehen — "
                                 f"eine Fake-Suche ist semantisch leer")

    def test_translation_is_dispatch_only(self):
        # Das MODELL sieht das Original nie: die Übersetzung lebt in einer
        # KOPIE der Args, das übergebene dict bleibt unberührt.
        self._cfg("refuse")
        m, org_fake = self._org_mapping()
        original_args = {"query": f"{org_fake} sanctions"}
        ref, args = self._guard(original_args, m)
        self.assertIsNone(ref)
        self.assertIn(org_fake, original_args["query"],
                      "die Args des Modells dürfen NICHT mutiert werden")
        self.assertIsNot(args, original_args)

    def test_allcaps_registry_variant_also_released(self):
        # Sanktionslisten führen ALLCAPS — auch DIESE registrierte Variante
        # muss den Auto-Release bekommen (vorher: refused → stiller False
        # Negative im Sanktions-Abgleich).
        self._cfg("refuse")
        m = self._mapping()
        ps._entity_fake_organisation("Wiener Privatbank SE", m)
        fake_allcaps = m.forward["WIENER PRIVATBANK"]
        ref, args = self._guard({"query": f"{fake_allcaps} OFAC SDN"}, m)
        self.assertIsNone(ref, "die ALLCAPS-Registryform muss recherchierbar sein")
        # Die Rück-Übersetzung normalisiert auf die kanonische Schreibweise —
        # entscheidend ist, dass der ECHTE Name rausgeht und KEIN Fake.
        self.assertIn("Wiener Privatbank", args["query"])
        self.assertNotIn(fake_allcaps, args["query"])

    def test_known_original_org_passes_without_dialog(self):
        # Auch der ECHTE Firmenname (falls je gemappt) darf passieren —
        # die Policy lässt business_id ohnehin durch.
        self._cfg("ask")
        m = self._mapping()
        ps._entity_fake_organisation("Wiener Privatbank SE", m)
        ref, args = self._guard({"query": "Wiener Privatbank Firmenbuch"}, m)
        self.assertIsNone(ref, "kein Refusal, kein Consent-Dialog für Orgs")


class TestPersonFakeStillRefused(_Base):
    """DIE Regression, die M5 nicht brechen darf."""

    def _person_mapping(self):
        m = self._mapping()
        ps._entity_fake_name("Bonnie Stark", m)
        return m, m.forward["Bonnie Stark"]

    def test_person_fake_refused_in_every_mode(self):
        for mode in _MODES:
            with self.subTest(mode=mode):
                self._cfg(mode)
                m, pf = self._person_mapping()
                ref, args = self._guard({"query": f"{pf} fraud"}, m)
                self.assertIsNotNone(
                    ref, f"{mode}: ein PERSONEN-Fake muss refusen — er trifft "
                         f"echte FREMDE Personen (Gift-Evidenz)")

    def test_person_original_never_auto_released(self):
        self._cfg("refuse")
        m, _ = self._person_mapping()
        ref, args = self._guard({"query": "Bonnie Stark fraud"}, m)
        self.assertIsNotNone(ref, "der echte Personenname darf NIE rausgehen")

    def test_person_fake_never_translated_into_args(self):
        self._cfg("allow")
        m, pf = self._person_mapping()
        ref, args = self._guard({"query": f"{pf} fraud"}, m)
        self.assertIsNotNone(ref)
        self.assertNotIn("Bonnie Stark", args.get("query", ""),
                         "ein Refusal darf die Klarwerte nicht heraus reichen")

    def test_mixed_query_person_wins(self):
        # Firma + Person in EINER Query: die Person muss den Call kippen,
        # sonst wäre der Auto-Release ein Schlupfloch.
        self._cfg("refuse")
        m = self._mapping()
        ps._entity_fake_organisation("Wiener Privatbank SE", m)
        ps._entity_fake_name("Bonnie Stark", m)
        of, pf = m.forward["Wiener Privatbank"], m.forward["Bonnie Stark"]
        ref, args = self._guard({"query": f"{of} {pf}"}, m)
        self.assertIsNotNone(
            ref, "eine Query mit Personen-Fake refust, auch wenn eine Firma "
                 "darin auto-released würde")


class TestAdHocProtectionWithoutProject(_Base):
    """M10b/G13 — der Egress-Schutz greift ohne Projekt/Preset/Mapping."""

    def test_regex_pii_refused_without_mapping(self):
        # DER belegte Turn-1-Leak der projektlosen Sessions — hier über einen
        # REGEX-erkennbaren Wert, damit der Test nicht von geladenen spaCy-
        # Modellen abhängt (siehe test_name_protection_needs_ner).
        self._cfg("refuse")
        ref, _ = self._guard({"query": "kontakt bonnie.stark@example.com pruefen"})
        self.assertIsNotNone(
            ref, "projektlos + Scanner an: die Mailadresse darf NICHT googeln")

    def test_name_protection_needs_ner(self):
        # EHRLICHE ABHÄNGIGKEIT (CLAUDE.md Regel 12): ein bloßer NAME ist
        # NER-only — es gibt keine Namens-Regex. Ohne geladenes spaCy-Modell
        # schützt M10b Namen NICHT. Im Betrieb lädt der Server die Modelle
        # beim Boot, dort greift der Schutz; hier wird die Abhängigkeit
        # festgehalten, statt sie zu verstecken.
        from engine import pii_ner as _pn
        self._cfg("refuse")
        self.assertIsNone(self._guard({"query": "Bonnie Stark Betrug"})[0],
                          "ohne NER kein Namens-Schutz — bewusst dokumentiert")
        _pn.load_models()
        try:
            if not _pn.is_available("de"):
                self.skipTest("spaCy-Modell nicht installiert")
            self.assertIsNotNone(
                self._guard({"query": "Bonnie Stark Betrug"})[0],
                "MIT NER muss der projektlose Klarname refusen (G13)")
        finally:
            _pn._NLP_CACHE.clear()

    def test_gate_stays_off_when_scanner_disabled(self):
        # Der Aus-Schalter bleibt der Aus-Schalter.
        self._cfg("refuse", enabled=False)
        ref, _ = self._guard({"query": "kontakt bonnie.stark@example.com pruefen"})
        self.assertIsNone(ref, "Scanner aus ⇒ Gate inaktiv (unverändert)")

    def test_technical_query_still_passes(self):
        # FP-Kosten: die Hälfte der realen Queries ist technisch.
        self._cfg("refuse")
        ref, _ = self._guard({"query": "ICAO 9303 check digit algorithm"})
        self.assertIsNone(ref)

    def test_company_query_passes_without_mapping(self):
        # business_id lässt die Policy durch — auch ohne Mapping.
        self._cfg("refuse")
        ref, _ = self._guard({"query": "Wiener Privatbank SE Firmenbuch"})
        self.assertIsNone(ref)


class TestAuditHonesty(_Base):
    def test_policy_release_is_audited_as_egress_not_blocked(self):
        # Ein Auto-Release ist ein EGRESS-Ereignis. Würde er als 'blocked'
        # getallied, log das Audit eine Blockade, die nie stattfand.
        self._cfg("refuse")
        m = self._mapping()
        ps._entity_fake_organisation("Wiener Privatbank SE", m)
        of = m.forward["Wiener Privatbank"]
        with request_context():
            ctx = get_request_context()
            ctx._gdpr_mapping_id = m.mapping_id
            ctx.current_session_id = None
            brain._gdpr_guard_web_args("searxng_search", {"query": f"{of} x"})
            d = ctx._gdpr_degradation or {}
        self.assertGreaterEqual(int(d.get("web_policy_released", 0)), 1)
        self.assertEqual(int(d.get("web_blocked", 0)), 0,
                         "ein Auto-Release ist KEINE Blockade")


if __name__ == "__main__":
    unittest.main(verbosity=2)
