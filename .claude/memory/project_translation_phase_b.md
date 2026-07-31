---
name: Translation Sektion — Phase B geshipped
description: Document translation (docx/pptx/pdf) live — chunked OOXML in-place edit + SSE-Progress + artifact-folder output. Phase C (Audio/Video + Live-Mic) noch ausstehend.
type: project
originSessionId: 4cd78ca9-258c-4879-a303-c9b9cf0d9cb3
---
2026-05-05 — Phase B abgeschlossen, alle 3 Formate validiert.

**Pipeline-Architektur:**
- `server_lib/translate/document.py` — docx/pptx via in-place OOXML edit: zip öffnen, alle `<w:t>` / `<a:t>` Runs über body+headers+footers+notes+slide-masters einsammeln (cross-part), in einem Pass übersetzen (chunked à 50 Runs), zurückschreiben, neu zippen. Layout/Fonts/Tables/Images bleiben identisch.
- **PDF Pipeline mit Layout-Erhalt**: `pdf2docx.Converter` konvertiert PDF zuerst in ein layout-faithful DOCX (Text-Frames, Tabellen, Spalten, Bilder positioniert wie im Original), dann läuft die Standard-DOCX-Pipeline drauf. Output ist `.docx` (nicht `.pdf` — PDF-output von übersetztem Text geht nicht ohne Layout-Schaden). Fallback-Cascade falls pdf2docx fehlt oder scheitert: markitdown → pymupdf (`fitz`) → pdfplumber → markdown→docx via `tool_write_document`. Erste-User-Iteration hatte nur den Plain-Text-Pfad — Layout total verloren — daher Upgrade auf pdf2docx als Default.
- pdf2docx wichtig: `print()` direkt zu stdout/stderr (NICHT logging), muss mit `contextlib.redirect_stdout/stderr(StringIO())` umschlossen werden sonst flutet jeder PDF-Job 500 INFO-Zeilen ins Daemon-Log. ~5-15s Konvertierungs-Overhead pro PDF (vor der Übersetzung).
- Chunked translation: numbered-list framing `[1] ... [2] ...` an einen einzigen `_run_delegate`-Call pro Chunk. Bei Parse-Fail per-run-Fallback via `translate_text`, damit Content nie verloren geht. Glossary applied per Chunk.
- `server_lib/translate/jobs.py` — `JobRegistry` singleton (process-wide, 1h TTL sweeper, lazy thread spawn). Subscriber-Queues pro Job für SSE.
- `translate_document` Tool — relative Pfade resolven gegen aktuelle Artifact-Folder (mirrors `write_file`); Output-Datei landet im Artifact-Folder, registriert via `_after_file_write`.
- HTTP: `POST /v1/translate/document` (multipart, daemon-thread spawn), `GET /v1/translate/jobs/<id>` (SSE), `GET /v1/translate/jobs/<id>/result` (octet-stream download).
- UI: Document-Tab live, drag&drop + file-picker fallback, native EventSource für Progress (kein Custom-Polling), State-Pills, Reset/Download-Buttons.

**Why:**
- DOCX/PPTX in-place edit bewahrt Bank-Layout 1:1 — DeepL-Style. PDF kann keine Library round-trippen ohne Layout zu zerschießen, also bewusst Fallback auf .docx-Output (UI kommuniziert das via `fallback: true` flag).
- Chunked statt per-run: 100-1000× weniger LLM-Calls + cross-run-Kohärenz (Pronouns, Glossary-Konsistenz). 50/Chunk = balance aus context size, cost, retry-blast-radius.
- Artifact-Folder als Output-Sink: kein zusätzlicher Storage, automatisches Versioning, Browse-Grid + Miner sehen die Datei automatisch.

**How to apply:**
- Phase C startet aus diesem Stand. Audio/Video-Tab wird UI-Wrapper über bestehende `transcribe_audio` (mit translate_to). Live-Mic via WebSocket + MediaRecorder-Chunks → server-side Voxtral.
- Wenn man später `translate_text` chunkt (für lange Texteingaben), `_translate_chunks` aus document.py wiederverwenden — das ist die einzige Source of Truth für numbered-list batching.

**Bekannte Constraints (zukünftige Wartung):**
- `ET.register_namespace("w", DOCX_NS)` MUSS pro Pipeline-Call laufen vor Re-Write, sonst kommt `ns0:` raus und Word weigert sich zu öffnen.
- `<w:t xml:space="preserve">` muss gesetzt sein wenn die übersetzte Run leading/trailing whitespace hat, sonst kollidiert Word die Spaces. Wird im Code automatisch gemacht in `_translate_office_zip`.
- **Synthetic session_id Bug-Fix**: `_get_artifact_session_folder` nimmt `session_id[:8]`. Bei konstantem prefix wie `translate-<hex>` kollidieren alle Jobs in einem Folder. Aktueller Code generiert `tr<14 hex>` damit die ersten 8 chars eindeutig sind.
- PDF-Output-Extension wechselt auf `.docx` — UI zeigt Hint-Text, Download-Filename ist korrekt gesetzt via `Content-Disposition`.
- Multipart parser ist hand-rolled (`_parse_multipart` in handlers/translate.py) — `cgi` ist seit Python 3.13 entfernt; gleiche Mechanik wie `handlers/projects.py:_handle_project_image_upload`.
- Per-run-Cap `MAX_RUN_CHARS = 8000` schützt gegen pasted-blob-in-single-`<w:t>` Edge-Case.

**Smoke-Test (2026-05-05):**
DOCX/PPTX/PDF DE→EN, alle done. Banking-Terminologie verified: Eigenkapitalquote→equity ratio, Vorstand entlasten→Executive Board discharged, BaFin verbatim, Aufsichtsrat→Supervisory Board, Hauptversammlung→Annual General Meeting, Hauptversammlung entlasten→discharged at the AGM. PPTX hatte 90 Runs durch viele leere/whitespace-Runs in Slide-Masters — fast-path im chunker hat die ohne LLM-Call durchgeschoben.
