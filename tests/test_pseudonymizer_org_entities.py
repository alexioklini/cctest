"""M4 — Organisations-Entitäten (PII_PARITY_WAVE2_HANDOVER.md §M4 / G2).

Spiegelbild von tests/test_pseudonymizer_entities.py (Personen-Schicht L2).

Der Failure, den diese Schicht schliesst (G2): jede Oberflächenform einer Firma
bekam einen EIGENEN Fake. Damit brach
  - der Sanktions-/Registry-Abgleich (die Listen führen ALLCAPS-/Aliasformen —
    anderer String → anderer Fake → stiller FALSE NEGATIVE in einem
    REGULATORISCHEN Bericht),
  - die Konzern-/UBO-Struktur (die Mutter-Tochter-Beziehung steckt im
    NAMENS-ENTHALTENSEIN: 'Wiener Privatbank Immobilien GmbH' ⊂ 'Wiener
    Privatbank' — getrennte Fakes löschen sie).

Alle Fixtures sind ECHTE Oberflächenformen aus dem Golden-Material
(bcad56fa99f8 WPB-Konzern, 32e257377809 ABACO-Registry, 748f92cfeacf 3SI,
65b4aefeed11 LEBC-Trust) — offline gegen den echten Scanner gemessen, nicht
erfunden.

Run: python3 -m unittest tests.test_pseudonymizer_org_entities -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402
from engine import identity as I  # noqa: E402


class _OrgBase(unittest.TestCase):
    def setUp(self):
        self.m = ps.new_mapping()

    def tearDown(self):
        ps.close_mapping(self.m.mapping_id)

    def fake(self, form):
        return ps._entity_fake_organisation(form, self.m)

    def orgs(self):
        return [e for e in self.m.entities.values() if e.get("kind") == "org"]


class TestOrgNormalisation(unittest.TestCase):
    """Die Normalform ist der Entitäts-Schlüssel — hier wird sie festgenagelt."""

    def test_legal_form_never_part_of_identity(self):
        # 'Wiener Privatbank SE' und 'Wiener Privatbank' sind DIESELBE Firma.
        # (Der Scanner liefert real die Spanne OHNE 'SE' — gemessen.)
        self.assertEqual(I.org_tokens("Wiener Privatbank SE"),
                         I.org_tokens("Wiener Privatbank"))

    def test_allcaps_registry_form_is_same_identity(self):
        # Sanktions-/Registry-Listen führen ALLCAPS. Wäre das eine eigene
        # Entität, liefe der Abgleich gegen einen anderen Fake ins Leere.
        self.assertEqual(I.org_tokens("NOVARO SUPPLY LLP"),
                         I.org_tokens("Novaro Supply LLP"))

    def test_digit_tokens_survive(self):
        # '3SI'/'K5' sind Teil des Namens — anders als bei Personen dürfen
        # Ziffern-Tokens NICHT verworfen werden.
        self.assertEqual(I.org_tokens("3SI Holding"), ["3si", "holding"])
        self.assertIn("k5", I.org_tokens("K5 Beteiligungs GmbH."))

    def test_trailing_sentence_period(self):
        # Real beobachtet: 'K5 Beteiligungs GmbH.' (mit Satzpunkt).
        self.assertEqual(I.org_tokens("K5 Beteiligungs GmbH."),
                         I.org_tokens("K5 Beteiligungs GmbH"))

    def test_noise_prefix_stripped(self):
        # Der NER-Span trägt real Müll-Präfixe ('ENTWURF JA 3SI Holding').
        self.assertEqual(I.org_tokens("ENTWURF JA 3SI Holding"),
                         I.org_tokens("3SI Holding"))

    def test_name_bearing_words_are_not_legal_forms(self):
        # REGRESSION (erste Fassung dieser Schicht): 'Holding'/'Partner'/
        # 'Invest' als Rechtsform zu strippen kollabierte die DREI 3SI-
        # Schwestern auf den Stamm ['3si'] und verschmolz sie zu EINER Firma.
        a = I.org_tokens("3SI Holding")
        b = I.org_tokens("3SI Partner GmbH")
        c = I.org_tokens("3SI Invest GmbH")
        self.assertNotEqual(a, b)
        self.assertNotEqual(b, c)
        self.assertNotEqual(a, c)

    def test_generic_word_alone_is_not_a_company(self):
        # Der spaCy-ORG-Tagger wirft gewöhnliche Substantive als Firmen aus
        # (gemessen: 'Trust' aus 'verwaltet den Trust'). Leerer Stamm ⇒ der
        # Aufrufer legt KEINE Entität an und fakt den Wert nicht.
        self.assertEqual(I.org_structure("Trust")[0], [])
        self.assertEqual(I.org_structure("Holding")[0], [])
        # Mehrtoken-Formen, die so ein Wort ENTHALTEN, bleiben Firmen.
        self.assertTrue(I.org_structure("Intertrust Group")[0])

    def test_public_bodies_are_never_faked(self):
        # Behörden/Register/Prüflisten sind das PRÜFWERKZEUG, nicht das
        # Prüfsubjekt. Sie zu faken ist kein Leak, aber eine QUALITÄTS-
        # Regression: im Live-E2E wurde 'In der OFAC-SDN-Liste steht …' zu
        # 'In der Oscorp Corp steht …' — das Modell verlor die Liste, gegen
        # die es abgleichen soll. (Unter `kyc` fiel das nie auf, weil Orgs
        # dort im Klartext bleiben; erst `screening` macht es sichtbar.)
        for body in ("OFAC-SDN-Liste", "Firmenbuch", "Companies House",
                     "EU-Kommission", "Interpol", "BaFin"):
            self.assertEqual(I.org_structure(body)[0], [],
                             f"{body!r} ist eine Behörde/Liste — nicht faken")
        # Eine echte Firma bleibt eine echte Firma.
        self.assertTrue(I.org_structure("Wiener Privatbank SE")[0])


class TestOrgEntityJoin(_OrgBase):
    """G2-Kern: alle Oberflächenformen EINER Firma → EIN Fake."""

    def test_all_surface_forms_one_entity(self):
        forms = ["Wiener Privatbank SE", "Wiener Privatbank",
                 "WIENER PRIVATBANK SE", "wiener-privatbank"]
        for f in forms:
            self.fake(f)
        stems = {tuple(e["stem"]) for e in self.orgs()}
        self.assertEqual(len(stems), 1,
                         f"alle Formen müssen EINE Entität sein, wurden: {stems}")

    def test_allcaps_and_title_share_one_fake_stem(self):
        # Der Registry-Abgleich steht und fällt damit.
        self.fake("Novaro Supply LLP")
        self.fake("NOVARO SUPPLY LLP")
        self.assertEqual(len(self.orgs()), 1)

    def test_two_different_companies_stay_distinct(self):
        # Die Umkehrung: KEIN False-Merge. Drei reale 'Atlantic Trading'
        # dürfen nicht auf einen Fake kollabieren (G12, Gift-Evidenz).
        self.fake("Wiener Privatbank SE")
        self.fake("Erste Bank AG")
        fakes = {tuple(e["fake_stem"]) for e in self.orgs()}
        self.assertEqual(len(self.orgs()), 2)
        self.assertEqual(len(fakes), 2, "zwei echte Firmen, zwei Fakes")

    def test_variants_are_real_forward_reverse_entries(self):
        # DIE Invariante (Handover §M4): nur registrierte Paare machen den
        # L3a-Args-Deanon UND das Web-Egress-Gate (→ M5) org-fähig, OHNE dass
        # dort Code angefasst wird.
        self.fake("Wiener Privatbank SE")
        for real in ("Wiener Privatbank", "WIENER PRIVATBANK",
                     "wiener-privatbank"):
            self.assertIn(real, self.m.forward,
                          f"{real!r} muss ein echter forward-Eintrag sein")
            self.assertIn(self.m.forward[real], self.m.reverse)

    def test_rule_id_is_organisation(self):
        # M5 entscheidet über die KATEGORIE des rule_id — sie muss stimmen,
        # sonst greift der Auto-Release nicht.
        self.fake("Wiener Privatbank SE")
        self.assertEqual(self.m.categories.get("Wiener Privatbank"),
                         "organisation")


class TestGroupStructureMirrored(_OrgBase):
    """G2: die Konzernstruktur steckt im Namens-Enthaltensein — der Fake muss
    sie SPIEGELN, sonst ist sie im anonymisierten Raum unsichtbar."""

    def test_parent_and_subsidiary_are_distinct_entities(self):
        self.fake("Wiener Privatbank SE")
        self.fake("Wiener Privatbank Immobilien GmbH")
        self.assertEqual(len(self.orgs()), 2,
                         "Mutter und Tochter sind ZWEI Firmen")

    def test_subsidiary_inherits_parent_fake_stem(self):
        # Mutter 'Nordstern Bank' → Tochter 'Nordstern … Immobilien':
        # ohne das ist die Mutter-Tochter-Beziehung gelöscht.
        self.fake("Wiener Privatbank SE")
        self.fake("Wiener Privatbank Immobilien GmbH")
        parent = next(e for e in self.orgs() if len(e["stem"]) == 2)
        child = next(e for e in self.orgs() if len(e["stem"]) == 3)
        self.assertEqual(child["fake_stem"][:2], parent["fake_stem"],
                         "die Tochter muss den Fake-Stamm der Mutter erben")

    def test_mirroring_works_in_both_discovery_orders(self):
        # Die Tochter kann im Dokument VOR der Mutter auftauchen.
        self.fake("Wiener Privatbank Immobilien GmbH")
        self.fake("Wiener Privatbank SE")
        parent = next(e for e in self.orgs() if len(e["stem"]) == 2)
        child = next(e for e in self.orgs() if len(e["stem"]) == 3)
        self.assertEqual(child["fake_stem"][:2], parent["fake_stem"])

    def test_siblings_share_stem_but_stay_distinct(self):
        # 3SI Holding / 3SI Partner / 3SI Invest: gemeinsamer Stamm im Fake
        # (sie GEHÖREN zusammen), aber drei unterscheidbare Firmen.
        for f in ("3SI Holding", "3SI Partner GmbH", "3SI Invest GmbH"):
            self.fake(f)
        orgs = self.orgs()
        self.assertEqual(len(orgs), 3)
        heads = {e["fake_stem"][0] for e in orgs}
        self.assertEqual(len(heads), 1, "Schwestern teilen den Fake-Stamm")
        self.assertEqual(len({tuple(e["fake_stem"]) for e in orgs}), 3,
                         "…bleiben aber drei unterscheidbare Firmen")


class TestOrgFakeHygiene(_OrgBase):
    """Fakes dürfen keine neuen Probleme erzeugen."""

    def test_fake_tokens_are_not_generic_corporate_words(self):
        # REGRESSION: ein Fake-Token, das SELBST ein generisches Konzernwort
        # ist (Trust/Holding/Group), wird beim nächsten Scan als frische
        # Org-PII erkannt und ein ZWEITES Mal gefakt — Fakes-von-Fakes, das
        # bricht den Reply-Deanonymisierer (gemessen: 'Nordstern Trust' →
        # 'NORDSTERN Stark Corp').
        for f in ("Wiener Privatbank SE", "3SI Holding", "3SI Partner GmbH",
                  "ABACO OVERSEAS HOLDINGS INC.", "Intertrust Group"):
            self.fake(f)
        for e in self.orgs():
            for tok in e["fake_stem"]:
                self.assertNotIn(tok.lower(), I._ORG_GENERIC_SOLO,
                                 f"Fake-Token {tok!r} ist ein generisches "
                                 f"Konzernwort → wird re-gefakt")

    def test_org_fake_never_collides_with_person_fake(self):
        ps._entity_fake_name("Bonnie Stark", self.m)
        self.fake("Wiener Privatbank SE")
        person = next(e for e in self.m.entities.values()
                      if e.get("kind", "person") == "person")
        ptoks = {person["fake_sur"].lower()}
        ptoks |= {g.lower() for g in person["fake_givens"]}
        for e in self.orgs():
            for tok in e["fake_stem"]:
                self.assertNotIn(tok.lower(), ptoks,
                                 "Firmen- und Personen-Fake dürfen nie auf "
                                 "denselben String fallen")

    def test_person_layer_ignores_org_entities(self):
        # Die Personen-Funktionen dürfen Org-Entitäten nie als Person lesen.
        self.fake("Wiener Privatbank SE")
        self.assertIsNone(ps._entity_find(self.m, "Wiener Privatbank"))

    def test_render_preserves_case_and_legal_form(self):
        self.fake("Wiener Privatbank SE")
        allcaps = self.fake("WIENER PRIVATBANK SE")
        self.assertTrue(allcaps.isupper(),
                        "die ALLCAPS-Registryform muss ALLCAPS bleiben")
        self.assertTrue(allcaps.endswith("SE"),
                        "die Rechtsform bleibt verbatim")


class TestLegacyMappings(unittest.TestCase):
    def test_entity_without_kind_is_treated_as_person(self):
        # Mappings, die VOR M4 auf Platte serialisiert wurden, tragen kein
        # `kind` — sie müssen weiter als Personen funktionieren.
        m = ps.new_mapping()
        try:
            m.entities["e1"] = {"sur": "stark", "givens": ["bonnie"],
                                "fake_sur": "Miller", "fake_givens": ["Erika"]}
            self.assertTrue(ps._is_person_ent(m.entities["e1"]))
            self.assertIsNotNone(ps._entity_find(m, "Bonnie Stark"))
        finally:
            ps.close_mapping(m.mapping_id)

    def test_entities_roundtrip_through_serialisation(self):
        m = ps.new_mapping()
        try:
            ps._entity_fake_organisation("Wiener Privatbank SE", m)
            m2 = ps._deserialize_mapping(ps._serialize_mapping(m))
            orgs = [e for e in m2.entities.values() if e.get("kind") == "org"]
            self.assertEqual(len(orgs), 1)
            self.assertEqual(orgs[0]["stem"], ["wiener", "privatbank"])
        finally:
            ps.close_mapping(m.mapping_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
