"""Tests for the .ipynb ingest path (Quant-Workbench Phase C).

Contract: `_extract_ipynb` is stdlib-json (NO nbformat), markdown cells go
verbatim, code cells become ```-fences with the kernelspec language, outputs
contribute text/plain only (images/HTML are render-time concerns). `.ipynb`
is in SUPPORTED_EXTS + _EXTRACTORS but NOT in the markitdown set — project
mining, PII scan and classification all reach it through the one
`_do_extract` dispatcher.

Run: python3 -m unittest tests.test_ipynb_extract -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import doc_convert  # noqa: E402


def _write_nb(path, cells, language="python"):
    nb = {
        "nbformat": 4, "nbformat_minor": 5,
        "metadata": {"kernelspec": {"language": language, "name": language}},
        "cells": cells,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(nb, f)
    return path


class TestExtractIpynb(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ipynb_test_")

    def test_registered_not_markitdown(self):
        self.assertIn(".ipynb", doc_convert.SUPPORTED_EXTS)
        self.assertIn(".ipynb", doc_convert._EXTRACTORS)
        self.assertNotIn(".ipynb", doc_convert._MARKITDOWN_EXTS)

    def test_md_code_and_outputs(self):
        p = _write_nb(os.path.join(self._tmp, "a.ipynb"), [
            {"cell_type": "markdown", "source": ["# VaR-Analyse\n", "Bericht."]},
            {"cell_type": "code",
             "source": ["import numpy as np\n", "print(np.mean([1, 2]))"],
             "outputs": [
                 {"output_type": "stream", "text": ["1.5\n"]},
                 {"output_type": "display_data",
                  "data": {"image/png": "iVBORw0KGgo=",
                           "text/plain": ["<Figure 640x480>"]}},
             ]},
        ])
        text, err = doc_convert._extract_ipynb(p)
        self.assertIsNone(err)
        self.assertIn("# VaR-Analyse\nBericht.", text)
        self.assertIn("```python\nimport numpy as np\nprint(np.mean([1, 2]))\n```", text)
        self.assertIn("```\n1.5\n```", text)
        self.assertIn("<Figure 640x480>", text)
        self.assertNotIn("iVBORw0KGgo", text)  # never base64 into mining text

    def test_r_kernel_language_fence(self):
        p = _write_nb(os.path.join(self._tmp, "r.ipynb"),
                      [{"cell_type": "code", "source": "x <- 1", "outputs": []}],
                      language="R")
        text, err = doc_convert._extract_ipynb(p)
        self.assertIsNone(err)
        self.assertIn("```R\nx <- 1\n```", text)

    def test_broken_json_fails_loud(self):
        p = os.path.join(self._tmp, "broken.ipynb")
        with open(p, "w") as f:
            f.write("{not json")
        text, err = doc_convert._extract_ipynb(p)
        self.assertEqual(text, "")
        self.assertIn("ipynb parse failed", err)

    def test_do_extract_dispatches(self):
        p = _write_nb(os.path.join(self._tmp, "b.ipynb"),
                      [{"cell_type": "markdown", "source": "Hallo"}])
        text, method, err = doc_convert._do_extract(p, use_markitdown=True)
        self.assertIsNone(err)
        self.assertIn("Hallo", text)


if __name__ == "__main__":
    unittest.main()
