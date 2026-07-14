"""doc_checks-Toolset (L1) — Golden-Tests gegen das ECHTE Material.

Die MRZ-Prüfziffern stammen aus dem Referenz-Chat 58e3c521438a (Handover §L1):
alter US-Pass 2007 (`3099879889USA4702058F1701186`) und neuer US-Pass 2026
(`5606837078USA4702058F2701264`) — der Chat hat beide manuell als gültig
bestätigt; beide MÜSSEN `all_valid` liefern. Eine absichtlich verfälschte MRZ
muss `false` liefern (sonst wäre das Tool ein Echtheits-Stempel für alles).

Die Namens-Normalisierung wird gegen die 8 Oberflächenformen aus dem
Failure-Katalog F1 getestet.

Run: python3 -m unittest tests.test_doc_checks -v
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.tools.doc_checks import (  # noqa: E402
    mrz_check_digit, parse_mrz, parse_date_value, _human_delta_dates,
    _strip_mrz_filler_garble, tool_mrz_verify, tool_doc_dates_check,
)
from engine import identity  # noqa: E402

# Golden MRZ line 2 (Handover §L1 Verifikation)
NEW_PASS_L2 = "5606837078USA4702058F2701264"
OLD_PASS_L2 = "3099879889USA4702058F1701186"


def _full_td3():
    """Synthetische, prüfziffern-KORREKTE Voll-MRZ zum echten Material."""
    l2 = NEW_PASS_L2 + "<" * 14 + "0"
    comp = mrz_check_digit(l2[0:10] + l2[13:20] + l2[21:43])
    l2 += str(comp)
    l1 = "P<USASTARK<<BONNIE<MARIE" + "<" * 20
    return l1 + "\n" + l2


class TestCheckDigit(unittest.TestCase):
    def test_golden_check_digits(self):
        """Die ICAO-9303-Mathematik am echten Material."""
        self.assertEqual(mrz_check_digit("560683707"), 8)
        self.assertEqual(mrz_check_digit("309987988"), 9)
        self.assertEqual(mrz_check_digit("470205"), 8)   # DOB beider Pässe
        self.assertEqual(mrz_check_digit("270126"), 4)   # Expiry neu
        self.assertEqual(mrz_check_digit("170118"), 6)   # Expiry alt

    def test_filler_is_zero(self):
        self.assertEqual(mrz_check_digit("<<<<<<"), 0)

    def test_letters(self):
        # A=10: "A" mit Gewicht 7 → 70 mod 10 = 0
        self.assertEqual(mrz_check_digit("A"), 0)


class TestParseMrz(unittest.TestCase):
    def test_new_pass_partial_line(self):
        p = parse_mrz(NEW_PASS_L2)
        self.assertIsNotNone(p)
        self.assertTrue(p["checks"]["document_number"])
        self.assertTrue(p["checks"]["dob"])
        self.assertTrue(p["checks"]["expiry"])
        self.assertEqual(p["nationality"], "USA")
        self.assertEqual(p["sex"], "F")
        self.assertEqual(p["expiry"], dt.date(2027, 1, 26))
        self.assertEqual(p["dob"], dt.date(1947, 2, 5))

    def test_old_pass_partial_line(self):
        p = parse_mrz(OLD_PASS_L2)
        self.assertTrue(p["checks"]["document_number"])
        self.assertTrue(p["checks"]["dob"])
        self.assertTrue(p["checks"]["expiry"])
        self.assertEqual(p["expiry"], dt.date(2017, 1, 18))

    def test_full_td3_all_five_valid(self):
        p = parse_mrz(_full_td3())
        self.assertEqual(p["format"], "TD3")
        self.assertEqual(p["surname"], "STARK")
        self.assertEqual(p["givens"], "BONNIE MARIE")
        checkable = [v for v in p["checks"].values() if v is not None]
        self.assertEqual(len(checkable), 5)
        self.assertTrue(all(checkable))

    def test_corrupted_mrz_fails(self):
        """Eine verfälschte Ziffer MUSS die Prüfziffer brechen — sonst wäre
        das Tool ein Echtheits-Stempel für alles."""
        bad = NEW_PASS_L2.replace("5606837078", "5606837178")
        p = parse_mrz(bad)
        self.assertIsNotNone(p)
        self.assertFalse(p["checks"]["document_number"])

    def test_ocr_confusion_in_date_field(self):
        """O→0 im Datumsfeld wird repariert (nur numerische Felder)."""
        garbled = NEW_PASS_L2.replace("470205", "47O205")
        p = parse_mrz(garbled)
        self.assertIsNotNone(p)
        self.assertTrue(p["checks"]["dob"])

    def test_embedded_in_ocr_noise(self):
        text = ("REISEPASS / PASSPORT\nType P Code USA\n"
                "Surname STARK\n" + _full_td3() + "\nirgendwas danach")
        p = parse_mrz(text)
        self.assertIsNotNone(p)
        self.assertTrue(p["checks"]["composite"])

    def test_no_mrz(self):
        self.assertIsNone(parse_mrz("Dies ist ein normaler Brief.\nMfG"))


class TestMrzVerifyTool(unittest.TestCase):
    def test_verdicts_are_pii_free(self):
        """Rückgabe OHNE Nummer, OHNE Namen, OHNE Roh-DOB (nur Alter)."""
        out = tool_mrz_verify({"text": _full_td3()})
        data = json.loads(out)
        self.assertTrue(data.get("mrz_found"))
        self.assertTrue(data.get("all_valid"))
        self.assertNotIn("560683707", out)
        self.assertNotIn("STARK", out)
        self.assertNotIn("BONNIE", out)
        self.assertNotIn("1947", out)
        self.assertIn("age_years", data)
        self.assertEqual(data["expiry_month"], "2027-01")
        self.assertEqual(data["expiry_state"],
                         "valid" if dt.date.today() <= dt.date(2027, 1, 26)
                         else "expired")

    def test_partial_line_verdict(self):
        out = json.loads(tool_mrz_verify({"text": NEW_PASS_L2}))
        self.assertTrue(out["mrz_found"])
        self.assertEqual(out["checksums_checkable"], 3)
        self.assertTrue(out["all_valid"])

    def test_corrupted_not_all_valid(self):
        bad = NEW_PASS_L2.replace("5606837078", "5606837178")
        out = json.loads(tool_mrz_verify({"text": bad}))
        self.assertTrue(out["mrz_found"])
        self.assertFalse(out["all_valid"])

    def test_requires_input(self):
        out = json.loads(tool_mrz_verify({}))
        self.assertIn("error", out)


class TestDateParsing(unittest.TestCase):
    def test_formats(self):
        """Alle Formate, die auf GENAU diesem Material vorkommen (F2/L2d)."""
        cases = {
            "2027-01-26": dt.date(2027, 1, 26),
            "27.01.2017": dt.date(2017, 1, 27),
            "27-01-2017": dt.date(2017, 1, 27),
            "01/27/2017": dt.date(2017, 1, 27),
            "5 FEB 1947": dt.date(1947, 2, 5),
            "05 Feb 1947": dt.date(1947, 2, 5),
            "26. Jan 2027": dt.date(2027, 1, 26),
            "19 JAN 2007": dt.date(2007, 1, 19),
            "2026:07:02 14:24:48": dt.date(2026, 7, 2),  # EXIF
            "26. Dez 2027": dt.date(2027, 12, 26),       # deutscher Monat
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_date_value(raw), want)

    def test_unparseable(self):
        self.assertIsNone(parse_date_value("gestern"))
        self.assertIsNone(parse_date_value(""))


class TestHumanDelta(unittest.TestCase):
    def test_ten_years_minus_one_day(self):
        """DER F2-Fall: 27.01.2017 → 26.01.2027 = exakt 10 J − 1 T.
        Eine 365-Tage-Näherung ergäbe fälschlich '10y + 1d'."""
        self.assertEqual(_human_delta_dates(dt.date(2017, 1, 27),
                                            dt.date(2027, 1, 26)), "10y - 1d")

    def test_renewal_gap_nine_days(self):
        self.assertEqual(_human_delta_dates(dt.date(2017, 1, 18),
                                            dt.date(2017, 1, 27)), "9 days")

    def test_negative(self):
        self.assertEqual(_human_delta_dates(dt.date(2026, 7, 7),
                                            dt.date(2026, 7, 2)), "-5 days")

    def test_exact_years(self):
        self.assertEqual(_human_delta_dates(dt.date(2017, 1, 27),
                                            dt.date(2027, 1, 27)), "10y")


class TestDocDatesCheckTool(unittest.TestCase):
    def test_renewal_and_span(self):
        out = json.loads(tool_doc_dates_check({
            "sources": [
                {"name": "old_expiry", "date": "18.01.2017"},
                {"name": "new_issue", "date": "27.01.2017"},
                {"name": "new_expiry", "date": "26.01.2027"},
            ],
            "pairs": [{"a": "old_expiry", "b": "new_issue"},
                      {"a": "new_issue", "b": "new_expiry"}],
        }))
        by_pair = {c["pair"]: c for c in out["checks"]}
        self.assertEqual(by_pair["old_expiry → new_issue"]["delta_days"], 9)
        self.assertEqual(by_pair["old_expiry → new_issue"]["delta"], "9 days")
        self.assertEqual(by_pair["new_issue → new_expiry"]["delta"], "10y - 1d")

    def test_dob_never_raw(self):
        """Geburts-benannte Quellen liefern NUR das Alter, nie das Datum."""
        out = json.loads(tool_doc_dates_check({
            "sources": [{"name": "dob", "date": "05.02.1947"},
                        {"name": "doc_date", "date": "07.07.2026"}],
        }))
        dob_entry = next(r for r in out["resolved"] if r["name"] == "dob")
        self.assertIn("age_years", dob_entry)
        self.assertNotIn("date", dob_entry)
        self.assertNotIn("1947", json.dumps(out))

    def test_exif_vs_doc_date(self):
        out = json.loads(tool_doc_dates_check({
            "sources": [{"name": "photo_taken", "date": "2026:07:02 14:24:48"},
                        {"name": "doc_date", "date": "07.07.2026"}],
            "pairs": [{"a": "photo_taken", "b": "doc_date"}],
        }))
        self.assertEqual(out["checks"][0]["delta_days"], 5)

    def test_unparseable_reported(self):
        out = json.loads(tool_doc_dates_check({
            "sources": [{"name": "x", "date": "kaputt"}]}))
        self.assertTrue(out.get("errors"))

    def test_requires_sources(self):
        self.assertIn("error", json.loads(tool_doc_dates_check({})))


class TestMrzGarbleCleanup(unittest.TestCase):
    """OCR-Garble-Formen, GEMESSEN am echten 10-JPG-Referenzsatz."""

    def test_x_as_filler_split(self):
        """'<' zwischen Vornamen als X gelesen (CF-Scan): BONNIEXMARIE."""
        self.assertEqual(_strip_mrz_filler_garble("BONNIEXMARIE"),
                         "BONNIE MARIE")

    def test_trailing_filler_token_dropped(self):
        self.assertEqual(
            _strip_mrz_filler_garble("BONNIE MARIE EERREREREKKERKEE"),
            "BONNIE MARIE")

    def test_intra_token_trailing_garble_cut(self):
        """Füller ohne Trenner an den letzten Vornamen geklebt (Foto 2).
        Die Schnittgrenze ist inhärent ambig (das End-E gehört zu Name UND
        Garble-Zeichensatz) — 'BONNTIMARTI' reicht: der Glued-Token-Fallback
        matcht es weiterhin auf die saubere Form (E2E am echten Material)."""
        self.assertEqual(_strip_mrz_filler_garble("BONNTIMARTIERERKKEKEES"),
                         "BONNTIMARTI")
        self.assertTrue(identity.names_match(
            "STARK, BONNTIMARTI", "STARK, BONNIE MARIE", fuzzy=True))

    def test_real_x_name_preserved(self):
        self.assertEqual(_strip_mrz_filler_garble("XAVIER"), "XAVIER")
        self.assertEqual(_strip_mrz_filler_garble("MARIA XAVIER"),
                         "MARIA XAVIER")


class TestGluedTokenMatching(unittest.TestCase):
    def test_glued_garble_matches(self):
        """Foto-2-Garble vs. saubere Form — der Glued-Token-Fallback."""
        self.assertTrue(identity.names_match(
            "STARK, BONNTIMARTIE", "STARK, BONNIE MARIE", fuzzy=True))

    def test_similar_but_different_people_no_match(self):
        """Der Fallback darf ähnliche ECHTE Namen nicht mergen."""
        self.assertFalse(identity.names_match(
            "Maria Huber", "Marion Huber", fuzzy=True))
        self.assertFalse(identity.names_match(
            "Stark, Bonnie Marie", "Stark, Connie Marie", fuzzy=True))


class TestIdentityNormalization(unittest.TestCase):
    """Die Oberflächenformen aus Failure F1 (Handover §1)."""

    FORMS_SAME = [
        ("STARK, BONNIE MARIE", "Bonnie M Stark"),
        ("Bonnie M Stark", "B. Stark"),
        ("STARK<<BONNIE<MARIE", "Bonnie M Stark"),
        ("Stark Bonnie M Mrs.", "STARK, BONNIE MARIE"),
        ("STARK_Bonnie_M_Mrs._107625", "Bonnie M Stark"),
    ]

    def test_same_person_matches(self):
        for a, b in self.FORMS_SAME:
            with self.subTest(a=a, b=b):
                self.assertTrue(identity.names_match(a, b, fuzzy=True))

    def test_mrz_name_parse(self):
        sur, giv = identity.parse_mrz_name("STARK<<BONNIE<MARIE")
        self.assertEqual(sur, "STARK")
        self.assertEqual(giv, "BONNIE MARIE")

    def test_different_person_no_match(self):
        self.assertFalse(identity.names_match("Erika Muster", "Bonnie M Stark",
                                              fuzzy=True))
        self.assertFalse(identity.names_match("Max Stark", "Bonnie M Stark",
                                              fuzzy=True))

    def test_bare_surname_no_match(self):
        """Einzelner Nachname allein matcht nicht (FP-Schutz)."""
        self.assertFalse(identity.names_match("Stark", "Bonnie M Stark"))

    def test_ocr_garble_stays_conservative(self):
        """'Bonnie MASE' liegt unter der konservativen Fuzzy-Schwelle —
        bewusst KEIN Match (False-Merge wäre schlimmer als ein Miss;
        Schwelle wird am echten 10-JPG-Satz kalibriert, Handover §7)."""
        self.assertFalse(identity.names_match("Bonnie MASE", "Bonnie M Stark",
                                              fuzzy=True))

    def test_cluster(self):
        forms = ["STARK, BONNIE MARIE", "Bonnie M Stark", "B. Stark",
                 "Erika Muster"]
        clusters = identity.cluster_names(forms)
        self.assertEqual(len(clusters), 2)
        main = max(clusters, key=len)
        self.assertEqual(len(main), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
