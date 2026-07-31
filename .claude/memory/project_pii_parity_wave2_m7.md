---
name: project_pii_parity_wave2_m7
description: "v9.346 M7/G5+G6 — Artefakt-Vollständigkeit (Fail-loud); Nicht-Baum-Reverse, .svg reversibel, Raster Fail-loud"
metadata: 
  node_type: memory
  type: project
  originSessionId: 148121fa-c7e1-4a21-963c-10d48b755d01
---

**v9.346.0 — PII-Parität Welle 2, M7 (G5+G6): Artefakt-Vollständigkeit.** L6 rückübersetzte nur Dateien im Artefakt-Baum in Text-Formaten; drei Kanäle fielen still durch. Ergänzt [[project_pii_parity_wave2_m4_m5]] und [[project_pii_parity_l_progress]]; die L6-Report-Fidelity-Mechanik bleibt gültig.

**Die drei Kanäle (alle in `handlers/chat.py` `make_gdpr_after_file_write_cb` + `engine/file_pseudonymize.py`):**
- **G5 Nicht-Baum:** der `_is_artifact_path`-Bail ENTFÄLLT. SCHLÜSSEL-EINSICHT: der Callback feuert AUSSCHLIESSLICH aus `brain._after_file_write`, das die Schreib-/Edit-Tools mit dem *gerade diesen Turn geschriebenen* Pfad rufen — fremde/vorbestehende Dateien erreichen ihn NIE. Also ist Reverse+Lint für jede modell-geschriebene Datei korrekt, Baum oder absoluter Pfad. Der alte Bail ließ eine HV-.docx mit erfundenen Aufsichtsrat-Namen ins Repo-Root still durch.
- **G6-a `.svg`:** `.svg` ∈ `SUPPORTED_EXTS` ∪ `_PLAIN_EXTS`. `file_pseudonymize.py` ist REVERSE-ONLY (forward-Walker längst retired, Text-side-Seam) → nur Reversibilität nötig; Plain-Text-String-Replace, Tokens sind XML-text-safe. `render_diagram` war SCHON in `GDPR_ARGS_DEANON_TOOLS` (v9.344, lokaler Renderer) → lokal gerendertes .svg trägt jetzt Echtwerte.
- **G6-b Raster:** `_GDPR_IMAGE_EXTS` (.png/.jpg/.jpeg/.webp/.gif). UNBEDINGT Fail-loud — KEIN Residuen-Check (Pixel ohne OCR nicht lesbar, anders als der PDF-Zweig `_GDPR_LINT_ONLY_EXTS`): Error-Zeile + Degradations-Zähler `image_unreversible` + `_gdpr_file_warnings`-Nudge (→ als .svg rendern). Der generische Drain in `llm_loop.dispatch_tool:748` reicht den Nudge ans Modell — deckt damit `generate_image` mit ab (dessen PROMPT separat über `EGRESS_TOOLS`/`_gdpr_scan_cloud_egress` gegatet ist, M2). Frontend: `chat_render.js` zeigt die Zeile im bestehenden Datenschutz-Streifen (ein `parts.push`, kein neues Global).

**Nicht-offensichtlich:** M7.4 (generate_image-Warnstreifen) war STRUKTURELL schon durch M7.3 erledigt — generate_image schreibt seine .png via `_after_file_write` → Image-Zweig → Nudge-Drain. Kein separater Code nötig. `render_diagram`-Args-Deanon + generate_image-Egress-Scan waren bereits in M3/M4 mit-erledigt (Handover Session-3-Note) — M7 offen blieben exakt die vier Punkte, Kern = Bail entfernen + zwei Ext-Mengen.

**Charakterisierungs-Tests, deren INTENT sich änderte** (in `test_chat_worker_helpers.py`): `skips_non_artifact`→`reverses_non_artifact` (Nicht-Baum wird jetzt rückübersetzt); `skips_unsupported` nutzt jetzt `.bin` statt `.png` (weil .png jetzt Fail-loud statt no-op). Mutationsgeprüft je Fix.

**OFFEN in Welle 2: nur noch M6** (Tabellen/Massendaten — Lasttest VOR Design, Reverse O(M²·T) bei Mapping-Bloat) **und M9** (Erkennungs-Netz — Sperrschrift/Transliteration/EN-NER). Handover `PII_PARITY_WAVE2_HANDOVER.md` § Session 4.
