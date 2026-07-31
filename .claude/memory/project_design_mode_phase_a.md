---
name: project_design_mode_phase_a
description: "v9.351-9.353 Design-Modus KOMPLETT: Phase A (Kommentar-Loop), B (Design-System pro Projekt, wire-only), C (PDF/PPTX-Export via Render-Service) — alle live"
metadata: 
  node_type: memory
  type: project
  originSessionId: 15c73b72-5d48-4fcf-8225-68c122815a34
---

**Design-Modus** (angelehnt an Claude Design, claude.com/product/design) — Phase A shipped 2026-07-16 als v9.351.0 (Commit 2da2fcec), Phase B als v9.352.0, BEWUSST ohne Layout-Editor (Ein-Schreiber-Invariante: nur der Agent schreibt das Artefakt; Drag/Resize hieße Zwei-Schreiber-Konflikt auf dem HTML-Quelltext). Der Original-Plan (`designmodusplan.html`) wurde vom User in der Phase-B-Session re-uploaded — Schema/Trigger/Generieren wurden EXAKT danach gebaut.

Phase A: `web/js/design_canvas.js` (EIN Global `DesignCanvas`). Kernentscheidungen:
- Artefakt-iframe ist `srcdoc` + `allow-same-origin` → Host instrumentiert `contentDocument` DIREKT (kein Script-Inject, kein postMessage).
- Pins werden bei JEDEM Re-Render verworfen (fail loud); nur auf der AKTUELLEN Version; Apply = normaler Chat-Turn via `sendMessage()`.

Phase B (v9.352.0): `project.json → design_system {colors:[{hex,role}], font_heading, font_body, logo_url, tone, css_snippet}` (Whitelist + Shape-Koerzierung in `update_project`).
- **Wire-only-Injektion**: `handlers/chat.py _build_design_context_preamble` → `_inject_web_preamble_into_wire` am Websuche-/Caveman-Seam; nur bei `body.design_context=true`; NIE System-Prompt, NIE History (live verifiziert: Marker nicht in DB; Turn ohne Flag sieht nichts).
- **Trigger deterministisch** (Plan §B): `DesignCanvas.isActive()` ODER Composer-Paletten-Toggle (`state.activeChat.designContext`, `toggleDesignContext` in init.js, newChat-Carry wie deepResearch) — Toggle deckt die ERST-Erstellung ab.
- **Generieren**: `POST .../design-system/generate {url|file(b64)|text}` (handlers/projects.py; server.py-Route MUSS vor dem generischen `endswith("/generate")` stehen). URL-Pfad holt ROH-HTML + bis 3 same-host-CSS (NICHT tool_web_fetch — Markdown strippt die Farben!); `background_call` cost_purpose `design_system_gen`, GDPR-Seam, review-before-save. Live-Test anthropic.com: echte Markenwerte (#CC785C, Pyretite/Copernicus) in 11s.
- **Abweichung vom Mockup** (bewusst): Logo als URL-Feld statt Datei-Upload — srcdoc-iframe kann Disk-Dateien nicht laden; Upload+Data-URI-Inlining erst nach Validierung.
- js_gate-Baseline 1997→2006 (+9 Globals dokumentiert).

Phase C (v9.353.0) — Export PDF/PPTX, Plan KOMPLETT:
- Render-Service (:8422) `POST /pdf {html}` + `POST /screenshot {html,selector}` treiben **Playwright direkt** (crawl4ai-eigene Flags können nur Full-Page, keine per-Element-Clips); eigener warmer Chromium neben dem Crawler; nach Service-Update `POST /v1/crawl4ai/restart` nötig.
- `GET /v1/artifacts/<id>/export?format=pdf|pptx&version=N`: pdf druckgenau (A4, prefer_css_page_size); pptx = Bild-Folien via python-pptx 16:9, EINE `<section data-slide>` = eine Folie, HARTES 422 ohne data-slide (keine geratenen Grenzen), Service down → 503, NIE Fallback-Render.
- `_DESIGN_DECK_CONVENTION` reitet auf JEDEM design_context-Turn (auch ohne design_system) — Agent schreibt exportfähige Decks. Export-Menü = `DesignCanvas.exportMenu` (im IIFE, null neue Globals).
- E2E grün: echtes report.html → valides PDF; 6-Folien-Deck → PPTX 6 Folien 13.333×7.5in, je 1 Vollbild; Konventions-Preamble vom Modell zitiert.

Incident v9.353.1 (erster echter Design-Apply, Chat 58e3c521): edit_file scheiterte am langen URL-encodierten SVG-data-URI (old_string-Mismatch, Rescues greifen nicht) → Modell wich auf `cd <Ordner> && python3 -c write_text` aus → Write unsichtbar, weil `_get_artifact_session_folder` TÄGLICH neu stempelte und die Mehrtages-Session am Tag 2 einen leeren Watch-Ordner bekam. FIX: existierender `*_<sid>`-Ordner wird wiederverwendet (Session = EIN Ordner lebenslang).

GESCHLOSSEN v9.353.2: edit_file **Rescue 3 `anchor-span`** (`_edit_rescue_anchors`) — Präfix/Suffix je 32 Zeichen verbatim, Span-Länge ±35%, difflib-Similarity ≥0.80, nur old_strings ≥200 Zeichen; Gold-Probe am echten fehlgeschlagenen Call grün (similarity 0.991).

**Nachtrag v9.360.0 (2026-07-17):** vierter Export-Weg **DOCX** (htmldocx + python-docx, in-process, kein Render-Service): echtes editierbares Word-Dokument (Headings/Tabellen/Listen bleiben Struktur, Layout bewusst vereinfacht). Zwei Pflicht-Guards: Chrome-Stripping (script/style/nav/button — sonst leaken Report-Toolbar+TOC als Text) + `_SafeHtmlToDocx.handle_img` (htmldocx 0.0.6 crasht an data-URIs; base64-Raster via BytesIO, SVG skippen). `pip install --break-system-packages htmldocx` per Maschine (wie python-pptx). **v9.361.0:** Export-Knopf auch auf PDF-Artefakten — einziger Weg dort DOCX via pdf2docx (Translate-Baustein, layout-treu; Bild-Scans → 422 mit OCR-Hinweis). UI-Gating über EXTENSION, nicht type (pdf hat type 'document'). PPTX→DOCX bewusst abgelehnt (Folien ≠ Fließtext; eigene PPTX = Bild-Folien).

**Nachtrag v9.364.0/9.364.1 (2026-07-17):** Bilder an Design-Kommentaren („füge diesen Screenshot hier ein") — die bei Phase B vertagte Upload+Data-URI-Inlining-Validierung ist damit erledigt. Mechanik: Kommentar-Bild reitet als normales Chat-Attachment (`state._pendingFiles` → GDPR-Scan → Disk; Bilder landen seit je AUCH bei Vision-Modellen auf Disk), Modell schreibt NUR `<img src="attachment://<name>">`, `brain._inline_attachment_refs` (oben in `_after_file_write`) ersetzt deterministisch durch data-URI — Bildbytes nie durchs Modell (200KB ≈ 70k Tokens). Guards: .html/.htm, Bild-Exts, 3MB/Bild, 5MB-Versions-Budget, Basename-only; Fail-loud via `RequestContext._design_file_warnings` (Drain in `dispatch_tool`, Präfix „⚠️ Design:"). Steering: Apply-Prompt + Satz in `_DESIGN_DECK_CONVENTION` (deckt auch Erst-Erstellung „One-Pager mit diesem Logo"). Live-E2E grün (glm-5.2; Kilo-Multimodal-400 besteht weiter — E2E mit non-vision-Modell fahren). **9.364.1:** DOCX-Export klammerte Bilder nicht — `add_picture` ohne width = native Pixelgröße (22–57 Zoll im WAG2018-Report); Post-Pass über `doc.inline_shapes` klammert auf nutzbare Breite UND Höhe.

**Offen / Beobachten:**
- Erfolgskriterien durch echte Nutzung validieren: B = zwei Entwürfe im selben Projekt ohne Nachsteuern markenkonsistent; C = PPTX öffnet sauber in echtem PowerPoint/Keynote (python-pptx-Roundtrip war grün, Nutzer-Gegenprobe aussteht).
- Falls Positions-Feintuning per Kommentar nervt: begrenzter Slide-Schema-Editor (nur Decks, x/y/w/h in data-Attributen) als separate Entscheidung.
- PPTX nativ-editierbar (Text/Shapes-Mapping) = bewusst V2, nur falls überhaupt nötig.
