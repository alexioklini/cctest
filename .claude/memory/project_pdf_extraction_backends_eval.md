---
name: project_pdf_extraction_backends_eval
description: "PDF→markdown backend eval 2026-06-16 (WPB Konzernbilanz) — pymupdf4llm WON (default), markitdown 2nd, docling rejected (138s/doc), marker deferred"
metadata: 
  node_type: memory
  type: project
  originSessionId: 99a1fa24-8a53-401a-80cc-9bc845d6506f
---

2026-06-16: evaluated PDF→markdown backends on the WPB Jahresfinanzbericht
(the doc behind the fc3fa95b incident), comparing the Konzernbilanz + risk tables.
Shipped pymupdf4llm as the default PDF engine (v9.142.0).

**Results (same WPB PDF, M2 Max):**
| backend | time | balance-sheet table | dep | license |
|---|---|---|---|---|
| **pymupdf4llm** ✅ WON | ~1-2s | clean, "Summe Aktiva" IN the table | fitz (already used) | AGPL |
| markitdown (old default) | ~1s | table but broken/half-rastered rows | CLI | MIT |
| docling | **138s** ✗ | clean BUT "Summe Aktiva 344.631.454" falls OUT of the table as loose text; RapidOCR fired "empty result" repeatedly on text pages | torch + ~300MB model (ds4sd/docling-models) + RapidOCR | MIT |
| fitz raw (page.get_text) | ~1s | fully flat, one cell/line | — | AGPL |
| marker | not run | — | torch + 3-5GB VRAM | OpenRAIL-M (commercial unclear) |

**Decision: pymupdf4llm is the default** (config.json conversion.pdf_engine,
3-way pymupdf4llm|markitdown|fitz, editable in Settings → Service-Modelle matrix).
Engine = _extract_pdf_pymupdf4llm() in engine/doc_convert.py, tried BEFORE
markitdown for .pdf, falls through to markitdown→fitz→OCR on empty/scanned.

**Why docling lost despite MIT license:** ~100× slower (138s vs 1-2s) —
untenable for interactive read_document AND mining; and its table extraction was
actually slightly WORSE here (totals row dropped out of the table). My earlier
"3-5GB VRAM like marker" was WRONG — docling is only ~300MB model + torch (MPS),
fits 32GB fine — but speed killed it. Possibly viable on DGX Spark w/ GPU, but no
quality advantage here. DON'T re-evaluate docling for this without a speed fix.

**Open: AGPL.** pymupdf4llm/PyMuPDF is AGPL-3.0 (Artifex). fitz was ALREADY in
use in _extract_pdf, so no NEW exposure, but clear commercial licensing for the
bank deployment independently. See [[project_marker_doc_extraction_eval]].
