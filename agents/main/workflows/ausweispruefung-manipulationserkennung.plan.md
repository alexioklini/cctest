# Ausführungsplan: Ausweisprüfung (Sicherheitsmerkmale & Manipulationserkennung)

### Schritt 1 — Verzeichnis & Bild laden
- Arbeitsverzeichnis prüfen; das hochgeladene Ausweisbild in den Arbeitsordner kopieren (z. B. `working_copy.jpg`).
- Metadaten extrahieren (Auflösung, Farbraum, EXIF, Software-Einträge, Erstellungs-/Änderungszeitstempel) — z. B. mit `magick identify -verbose`, `exiftool` oder Python/PIL.

### Schritt 2 — Dokumenttyp & Land bestimmen
- Bild visuell inspizieren (es ist als Inline-Content sichtbar). Land, Dokumentart (Reisepass/ID-Karte), Format und Layout erkennen.
- Erwartete Sicherheitsmerkmale für diesen Dokumenttyp ggf. per Webrecherche kurz verifizieren (Query: „[Land] [Dokumenttyp] security features ICAO 9303"). **Keine Vermutungen als Fakten ausgeben** — nur verifizierte Referenzmerkmale nennen.

### Schritt 3 — MRZ-Extraktion & Prüfziffer-Validierung
- MRZ-Region (unterer Bereich) mit Python/PIL zuschneiden.
- OCR mit `pytesseract` (`--psm 6`, Zeichen-Whitelist `A-Z0-9<`).
- Regex: 2–3 Zeilen × 44 Zeichen (TD3-Passformat) oder 3 × 30 (TD1).
- Mit Python ICAO-9303-Modulo-10-Algorithmus alle Prüfziffern verifizieren (Dokumentennummer, Geburtsdatum, Geschlecht, Gültigkeit, Gesamtprüfung).
- Ergebnisse in `mrz_check.txt` ablegen.

### Schritt 4 — Datenkonsistenz (VZ vs. MRZ)
- Sichtbare Felder (Name, Vorname, Geburtsdatum, Geschlecht, Ausstellungsdatum, Gültigkeit, Dokumentennummer, Staatsangehörigkeit) per OCR oder manueller Ablesung erfassen.
- Jedes Feld gegen entsprechendes MRZ-Feld abgleichen. Abweichungen markieren.

### Schritt 5 — Forensische Bildanalyse (Manipulation)
- **Error Level Analysis (ELA):** Bild bei Qualität ~90 neu als JPEG speichern, Differenzbild zum Original berechnen → `ela_result.jpg`. Bereiche mit abweichendem Kompressionslevel hervorgehoben.
- **Noise-Konsistenz:** Hochpassfilter / Laplacian-Varianz in Bildregionen (Foto vs. Hintergrund vs. Textfelder) berechnen; signifikante Abweichungen melden.
- **Kantenanalyse** um das Passfoto (Luma-Gradient, Halo-Effekte, unscharfe Ränder).
- **Klonerkennung / duplizierte Regionen:** optional mit `copy-move-forgery`-ähnlichem Ansatz oder visuelle Prüfung der Ausgaben.
- **EXIF-Konsistenz:** Software-Einträge wie „Photoshop", fehlende oder inkonsistente Kamera-Metadaten als Indikator erfassen (nicht als Beweis).

### Schritt 6 — Sicherheitsmerkmale (soweit am Foto beurteilbar)
- Pro Merkmal only als „am Foto sichtbar / nicht sichtbar / unklar" kennzeichnen. Zu prüfen:
  - Hologramm / Kinegramm / OVI-Elemente (Farbverschiebung, Regenhologramm-Muster)
  - Guillochen-Muster (durchgehende Linien, keine Brüche)
  - Mikroschrift (Zoom auf Rand- oder Hintergrundlinien; Verpixelung statt klarem Text = Indiz)
  - Sekundärbild / Geisterbild / Lasergravur
  - Wasserzeichen / Sicherheitsfaden (falls erkennbar)
  - Prägung / fühlbare Elemente (am Foto **nicht** prüfbar — explizit vermerken)
- Expliziter Hinweis: UV-/IR-Merkmale und Haptik sind **nur am physischen Dokument** überprüfbar.

### Schritt 7 — Referenzabgleich (Webrecherche)
- Falls in Schritt 2 Land/Typ identifiziert: gezielte Suche nach offiziellem Layout und bekannten Sicherheitsmerkmalen.
- Ergebnisse mit Quellenangaben in den Bericht einfließen lassen.