---
name: project_pdf_table_extraction_fix
description: "v9.381.0 PDF-Tabellen-Fix (Chat f3b8dc2f, Wiener-Privatbank-Erträgnisaufstellung) — include_tables war still wirkungslos, Tool-Prosa gegen Freihand-Transkription; randlose Mehrzeilen-Record-Tabellen schlagen ALLE Extraktoren"
metadata: 
  node_type: memory
  type: project
  originSessionId: d0844fbb-3acf-4cf2-9cf4-8ff6e803c091
  modified: 2026-07-20T10:14:12.938Z
---

**v9.381.0 (2026-07-20), Anlass Chat f3b8dc2f**: mistral-large-3 transkribierte eine 106-Zeilen-Banktabelle (Wiener Privatbank Erträgnisaufstellung) freihändig aus pymupdf4llm-Markdown → systematisch falsche Excel-Daten (~34k € Abweichung in der Tax-EUR-Spaltensumme; zwei gestapelte Steuerzeilen als Währungspaar fehlgemappt).

Nicht-offensichtliche Fakten:
- **Randlose Banktabellen mit Mehrzeilen-Records (2-zeiliger Header, 1 Transaktion = 2-4 visuelle Zeilen, Spaltenzahl driftet pro Seite) schlagen ALLE Extraktoren**: pdfplumber findet 0 Tabellen (braucht Linien), fitz `find_tables(strategy='text')` zerhackt Wörter mitten in Zellen, pymupdf4llm ist das Beste (textuell vollständig, aber visuelles Grid ≠ Records). Ein "besserer Extraktor" ist hier KEINE Option — der Fix ist Prosa + Ehrlichkeit.
- **`include_tables` war seit Einführung des pymupdf4llm-Default-Pfads stillschweigend WIRKUNGSLOS** (`_do_extract` returnte vor dem pdfplumber-Block). Seit 9.381.0 verdrahtet; Backend-Tag `pymupdf4llm+tables:<n>` — `tables:0` heißt: Inline-Markdown ist alles.
- **Fehlweg des Modells war prosa-induziert**: es folgte der CRITICAL-source-Prosa von xlsx_create, übergab das PDF als `source.file` (openpyxl-Error), `tool_search('pdf to excel')` fand nichts, DANN erst Freihand-Abtippen. Prosa sagt jetzt explizit "source.file can NOT read PDFs" + Regruppieren + xlsx_query-Summencheck gegen Summary-Zahlen der Quelle.
- User wollte **nur den Pipeline-Fix**, NICHT die Korrektur des falschen Artefakts (Transaktionshistorie_2025.xlsx in 2026-07-20_f3b8dc2f72f8 bleibt falsch). Fix live vom User bestätigt („now its much better", 2026-07-20).
- Damit ist der Blocker aus [[project_gdpr_all_checks_pre_dialog_plan]] („zurückgestellt für PDF-Tabellen-Fix") erledigt — der GDPR-Plan kann wieder aufgenommen werden.
