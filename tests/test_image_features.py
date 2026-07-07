"""Tests for deterministic, model-free image description (engine/image_features.py)
and the deterministic-first image-degrade pipeline (brain._describe_image_*).

WHY: when an image goes to a non-vision model, the degrade path now runs LOCAL
OCR + deterministic features (dimensions/EXIF/colours/faces) + QR/barcodes
BEFORE any vision LLM — the LLM is only a fallback when those signals are thin.
These tests pin: features are extracted without a model; a text image yields a
'strong' signal (so the vision LLM is skipped); a textless flat image is 'thin'
(so the fallback would fire).

Skips the OCR-dependent assertions when tesseract is unavailable; the pure
feature tests need only OpenCV/Pillow (always present).

Run: python3 -m unittest tests.test_image_features -v
"""

from __future__ import annotations

import base64
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


HAVE_OCR = _tesseract_available()


def _png_b64(img) -> str:
    b = io.BytesIO()
    img.save(b, format="PNG")
    return base64.b64encode(b.getvalue()).decode()


class ImageFeaturesTest(unittest.TestCase):

    def test_features_are_model_free_facts(self):
        from PIL import Image
        from engine.image_features import describe_image_features
        img = Image.new("RGB", (320, 240), (70, 130, 180))
        feat = describe_image_features(_png_bytes(img), "x.png")
        joined = " ".join(feat["facts"])
        self.assertIn("320×240", joined)          # dimensions
        self.assertIn("Helligkeit", joined)        # brightness
        self.assertIn("Typ=", joined)              # photo/graphic classification
        self.assertIsInstance(feat["faces"], int)
        self.assertIsInstance(feat["codes"], list)

    def test_unreadable_bytes_do_not_raise(self):
        from engine.image_features import describe_image_features
        feat = describe_image_features(b"not an image", "bad.png")
        self.assertFalse(feat["has_signal"])

    def test_qr_decode_via_opencv_no_crash(self):
        # QR/barcode decoding uses OpenCV (NOT pyzbar, which segfaults on this
        # Python). A blank image must return no codes WITHOUT crashing the
        # process — the crash-safety is the point of this test.
        from PIL import Image
        from engine.image_features import describe_image_features
        feat = describe_image_features(_png_bytes(Image.new("RGB", (80, 80), "white")))
        self.assertEqual(feat["codes"], [])

    def test_qr_decode_reads_content_when_generator_available(self):
        # If a pure-python QR generator is installed, prove real decoding.
        try:
            import segno  # noqa
            import io
            buf = io.BytesIO()
            segno.make("https://example.test/abc").save(buf, kind="png", scale=10, border=4)
            data = buf.getvalue()
        except ImportError:
            self.skipTest("no QR generator (segno) installed")
        from engine.image_features import describe_image_features
        feat = describe_image_features(data, "qr.png")
        self.assertTrue(any("example.test/abc" in c for c in feat["codes"]),
                        f"QR not decoded: {feat['codes']}")

    def test_features_to_text_renders_line(self):
        from PIL import Image
        from engine.image_features import (describe_image_features,
                                           features_to_text)
        img = Image.new("RGB", (100, 100), (10, 10, 10))
        line = features_to_text(describe_image_features(_png_bytes(img)), "d.png")
        self.assertIn("d.png:", line)
        self.assertIn("px", line)


class DegradePipelineTest(unittest.TestCase):
    """The deterministic-first decision in brain._describe_image_deterministic."""

    def setUp(self):
        import brain
        self.brain = brain

    @unittest.skipUnless(HAVE_OCR, "tesseract not installed")
    def test_text_image_is_strong_signal(self):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (700, 120), "white")
        ImageDraw.Draw(img).text((20, 40),
                                 "RECHNUNG Nr 2024-0815 Summe 119 EUR", fill="black")
        img = img.resize((1400, 240), Image.LANCZOS)   # give tesseract real pixels
        txt, strong = self.brain._describe_image_deterministic(
            _png_b64(img), "beleg.png")
        self.assertTrue(strong, "text image should be a strong signal (skip vision LLM)")
        self.assertIn("20240815", txt.replace("-", ""))  # hyphen is font/lang-dependent
        self.assertIn("OCR", txt)

    def test_textless_flat_image_is_thin(self):
        from PIL import Image
        img = Image.new("RGB", (300, 200), (128, 128, 128))
        txt, strong = self.brain._describe_image_deterministic(
            _png_b64(img), "foto.png")
        # flat grey, no text, no faces, no codes → thin → vision fallback fires
        self.assertFalse(strong)
        self.assertIn("Bildmerkmale", txt)          # but we still return facts

    def test_bad_base64_returns_empty(self):
        txt, strong = self.brain._describe_image_deterministic("!!!notb64!!!", "x")
        self.assertEqual(txt, "")
        self.assertFalse(strong)


def _png_bytes(img) -> bytes:
    b = io.BytesIO()
    img.save(b, format="PNG")
    return b.getvalue()


if __name__ == "__main__":
    unittest.main()
