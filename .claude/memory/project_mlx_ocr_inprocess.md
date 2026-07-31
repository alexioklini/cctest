---
name: project_mlx_ocr_inprocess
description: v9.328 — OCR = GLM-OCR in-process via mlx-vlm (nicht oMLX); MLX braucht EINEN langlebigen Thread (Thread-Exit crasht Metal); pymupdf4llm schob Scans heimlich durch Tesseract
metadata: 
  node_type: memory
  type: project
  originSessionId: fd1b7539-f6f4-4838-9e19-f7dda8c9c0d1
---

**v9.328.0 — In-Process-MLX-OCR (`engine/mlx_ocr.py`), Engine `mlx_ocr`.**

Default `mlx-community/GLM-OCR-4bit` (0,9B, 1,25 GB). Gemessen M4, gleicher Pass-Scan:
gemma-4-12B via oMLX **36,6s / ~7GB** → GLM-OCR-4bit in-process **1,2s / 2,3GB**, identisch
korrekt. 4bit == 8bit in der Qualität → das kleinere. mlx-vlm ≥0.6.4 (`glm_ocr`-Arch).

**Warum NICHT über oMLX** (User-Vorgabe): oMLX bleibt für gemma-4-12B + Cloud-Fallback —
dort zahlt sein SSD-KV-Cache. OCR hat weder Konversation noch KV-Prefix, gewinnt dort nichts
und konkurriert nur mit dem Chat um denselben Server. oMLX *könnte* es (lädt beliebige
MLX-Modelle aus `~/.omlx/models`, führt 22 Modelle diverser Familien) — wollten wir aber nicht.

## MLX-Threading-Invariante (KOSTETE EINEN DAEMON-CRASH)

MLX/Metal muss von **EINEM langlebigen Thread** getrieben werden. Zwei Absturzarten,
beide SIGSEGV in `libmlx.dylib`, beide reproduziert:
1. Zwei Threads rechnen gleichzeitig → Crash in `mlx::core::eval`.
2. **Ein Thread, der MLX benutzt hat und dann ENDET** → Crash in `_pthread_tsd_cleanup`
   (Metal baut Thread-Local-State ab).

**(2) schlägt auch bei per-Lock serialisierter Rechnung zu — ein Lock allein reicht NICHT.**
Das war mein erster, falscher Fix. Unsere Aufrufer sind Pool-Threads (`ingest_queue_*`),
die sterben. Lösung: ein Daemon-Thread `mlx-ocr` besitzt MLX, Aufrufer stellen Arbeit in
eine Queue und blocken (`_run_on_worker`). Auch `unload()` muss dort laufen (Freigeben von
MLX-Arrays aus Fremd-Thread crasht ebenso). Kostet nichts: eine GPU, parallel wäre eh nicht
schneller. **Gilt für jede künftige MLX-Nutzung in Brain, nicht nur OCR.**

## Tesseract-Falle (vorbestehend, gravierend)

`pymupdf4llm` schaltet bei einer Seite **ohne Textebene** SELBSTTÄTIG auf **Tesseract** um
("Using Tesseract for OCR processing") und gibt dessen Output zurück, als wäre er extrahierter
Text. Nicht-leer = Erfolg → **die konfigurierte OCR-Kette wurde NIE erreicht**; jeder Scan im
Korpus bekam still den schlechteren Text. Tesseract las am Test-Pass `05.02.**1847**`
(Jahrhundert falsch!) und `S6068370F` statt `560683707`.
Fix: `_pdf_has_no_text_layer()` (billig: `page.get_text()` rendert/OCRt nicht) → keine
Textebene ⇒ Output IST Tesseract ⇒ verwerfen, echte OCR laufen lassen. PDFs *mit* Textebene
unangetastet.

## Weitere Messwerte
- `mlx_ocr_max_edge_px` (Default 1600): 200-DPI-Render ≈2500px. Gemessen 2500px 14,7s /
  1600px 2,9s / 1200px 1,5s — bei **identischem** Text. Nur für gerenderte PDF-Seiten.
- **Whisper braucht KEINEN Cache**: `mlx_whisper.transcribe.ModelHolder` hat schon einen
  (hält ein Modell, lädt nur bei Modellwechsel). `mlx_vlm.load()` hat KEINEN → daher unser
  Ein-Slot-Holder.

Verwandt: [[project_llm_call_catalog]], [[feedback_never_sigkill_brain]]
