"""Tests for the deterministic local OCR toolset (ocr_inspect / ocr_extract /
ocr_region / ocr_fields / ocr_tables, engine/tools/ocr_tools.py).

WHY: attaching a scan used to give the model only a native "look" or a
free-text vision DESCRIPTION — both are the model reading pixels and re-typing
numbers. These tools run tesseract LOCALLY (no LLM, no cloud) and hand back
text-faithful output the model can reason about. Like the xlsx toolset, the
server does the deterministic work; the model supplies only intent (a regex, a
bbox, a mode).

The fixtures render synthetic bitmaps with PIL and OCR them. Small default-font
bitmaps OCR imperfectly, so the asserts key on ROBUST tokens (the invoice
number, the 'RECHNUNG' header) and on STRUCTURE (JSON shape, CSV grid, capped
preview, error handling) rather than pixel-perfect text.

Skips cleanly when pytesseract or the tesseract binary is unavailable.

Run: python3 -m unittest tests.test_ocr_tools -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        from PIL import Image  # noqa: F401
        return True
    except Exception:
        return False


HAVE_OCR = _tesseract_available()


def _render_receipt(path: str) -> None:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (700, 260), "white")
    d = ImageDraw.Draw(img)
    d.text((20, 20), "RECHNUNG Nr 2024-0815", fill="black")
    d.text((20, 60), "Kunde ACME GmbH", fill="black")
    d.text((20, 110), "Pos   Artikel   Menge   Preis", fill="black")
    d.text((20, 140), "1     Kabel     2       19", fill="black")
    d.text((20, 170), "2     Stecker   5       4", fill="black")
    d.text((20, 220), "Summe 119 EUR", fill="black")
    img.save(path)


@unittest.skipUnless(HAVE_OCR, "tesseract/pytesseract not installed")
class OCRToolsTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from engine.tools import ocr_tools
        cls.O = ocr_tools
        cls.tmp = tempfile.mkdtemp(prefix="ocrtest_")
        cls.png = os.path.join(cls.tmp, "receipt.png")
        _render_receipt(cls.png)

    def _obj(self, res_json: str) -> dict:
        """Parse an _ok() result to a dict (or raise on _err)."""
        obj = json.loads(res_json)
        self.assertIsInstance(obj, dict)
        self.assertNotIn("error", obj, f"tool returned error: {obj}")
        return obj

    def test_inspect_profiles_without_full_ocr(self):
        obj = self._obj(self.O.tool_ocr_inspect({"path": self.png}))
        self.assertEqual(obj["pages"], 1)
        self.assertIn("700x260", obj["report"])
        self.assertIn("mean_conf", obj["report"])

    def test_extract_reads_robust_tokens(self):
        obj = self._obj(self.O.tool_ocr_extract({"path": self.png}))
        # the digit run OCRs reliably; the tiny test font renders the hyphen
        # inconsistently across tesseract language packs, so assert on digits
        # (with or without the hyphen), not the exact "2024-0815" format.
        self.assertIn("20240815", obj["text"].replace("-", ""))
        self.assertIn("RECHNUNG", obj["text"].upper())
        self.assertIn("mean_confidence", obj)
        self.assertEqual(obj["pages"], 1)

    def test_extract_out_writes_artifact(self):
        # No chat session in a unit test, so _enforce_artifact_path falls back
        # to cwd — chdir into the temp dir so the artifact doesn't litter the
        # repo, and confirm the file is actually written.
        prev = os.getcwd()
        os.chdir(self.tmp)
        try:
            obj = self._obj(self.O.tool_ocr_extract(
                {"path": self.png, "out": "full.txt"}))
            self.assertEqual(obj["saved_to"], "full.txt")
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, "full.txt")))
        finally:
            os.chdir(prev)

    def test_region_limits_to_crop(self):
        # top strip contains only the header line
        obj = self._obj(self.O.tool_ocr_region(
            {"path": self.png, "bbox": [0, 0, 700, 45]}))
        self.assertIn("20240815", obj["text"].replace("-", ""))  # hyphen font-dependent
        self.assertNotIn("Summe", obj["text"])           # bottom line excluded

    def test_region_requires_bbox(self):
        obj = json.loads(self.O.tool_ocr_region({"path": self.png}))
        self.assertIn("error", obj)

    def test_fields_regex_extraction_deterministic(self):
        obj = self._obj(self.O.tool_ocr_fields({
            "path": self.png,
            "fields": [
                {"name": "rechnungsnr", "pattern": r"Nr\s+([\d-]+)"},
                {"name": "missing", "pattern": r"IBAN\s+(\S+)"},
            ],
        }))
        # digits are read reliably; the hyphen is font/lang-dependent
        self.assertEqual(obj["fields"]["rechnungsnr"].replace("-", ""), "20240815")
        self.assertIsNone(obj["fields"]["missing"])
        self.assertIn("missing", obj["unmatched"])

    def test_fields_requires_fields(self):
        obj = json.loads(self.O.tool_ocr_fields({"path": self.png}))
        self.assertIn("error", obj)

    def test_fields_bad_regex_is_reported_not_raised(self):
        obj = self._obj(self.O.tool_ocr_fields({
            "path": self.png,
            "fields": [{"name": "x", "pattern": r"([unclosed"}],
        }))
        self.assertIsNone(obj["fields"]["x"])

    def test_tables_emits_csv_grid(self):
        obj = self._obj(self.O.tool_ocr_tables({"path": self.png}))
        self.assertGreater(obj["rows"], 0)
        self.assertIn(",", obj["csv_preview"])           # CSV separators present

    def test_unsupported_type_errors(self):
        bad = os.path.join(self.tmp, "note.txt")
        with open(bad, "w") as f:
            f.write("hi")
        obj = json.loads(self.O.tool_ocr_extract({"path": bad}))
        self.assertIn("error", obj)

    def test_missing_file_errors(self):
        obj = json.loads(self.O.tool_ocr_extract({"path": "/tmp/does_not_exist.png"}))
        self.assertIn("error", obj)


if __name__ == "__main__":
    unittest.main()
