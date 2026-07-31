---
name: project_gdpr_xlsx_deanon_race
description: "BUG in Untersuchung — deanonymize_file/_xlsx_reverse scheitert an halb-geschriebener xlsx (Race), Deanon findet NICHT statt → Fake-Werte könnten geliefert werden"
metadata: 
  node_type: memory
  type: project
  originSessionId: 2a5d4fdd-4b90-4a02-922f-e7cdc4d16758
  modified: 2026-07-22T07:42:25.917Z
---

**ENTDECKT 2026-07-22** beim PII-Phase-1-Transaktions-Testlauf (Session 8709f19dbdc2, mistral-medium-3.5, Anon AN). Der User: der File-Pseudonymizer MUSS unter ALLEN Umständen korrekte Dateien liefern (echte Werte, nie Fakes, nie korrupt) — sonst ist der ganze Anonymisierungs-Prozess wertlos. Gleiche Klasse wie der docx-Bug v9.393.1 ([[project_gdpr_inplace_ooxml_destroy]]).

**SYMPTOM:** `FilePseudonymizeError: .xlsx walker failed: File is not a zip file` im After-File-Write-Deanon-Callback (`make_gdpr_after_file_write_cb`, handlers/chat.py → `pseudonymizer.deanonymize_file(path, path)` → `engine/file_pseudonymize.py:_xlsx_reverse:230`).

**BEFUND (DB-forensisch):** Dieselbe Datei `transaktions_historie_2025.xlsx` wurde 3× deanonymisiert (ids 20466/20470/20472). Der 2. Versuch warf den Zip-Fehler; davor+danach restored=0. Alle 3 xlsx auf Platte sind JETZT valide Zips (openpyxl lädt sie) → der Fehler war TEMPORÄR: die Datei war beim `openpyxl.load_workbook(src)` mitten im Schreiben (kein valides Zip). Race: Deanon-Callback trifft eine gerade geschriebene Datei.

**WARUM mehrfach:** python_exec (engine/tools/file_tools.py) erkennt via `_changed_files`-Diff ALLE geänderten Dateien im watch_dir und ruft `_after_file_write` (→Deanon) für jede — bei mehreren python_exec-Iterationen auf denselben Dateinamen feuert der Deanon mehrfach. python_exec läuft SYNCHRON (subprocess) → Race muss zwischen aufeinanderfolgenden Calls ODER durch parallele Tool-Calls (llm_loop parallel_tool_calls) entstehen. [Agent klärt genauen Pfad.]

**ECHTES RISIKO:** In DIESEM Fall harmlos (finale Datei korrekt, enthält echte 704783, keine Fakes — die betroffene Datei wurde danach nochmal korrekt deanonymisiert; nur 1 ID gemappt, gar nicht im Excel). ABER: wäre der fehlgeschlagene Versuch der LETZTE gewesen, hätte der Nutzer eine Datei mit FAKES (nicht deanonymisiert) bekommen — die "wertlos"-Situation. `_xlsx_reverse` hat KEINEN Temp-File-Schutz wie `_walk_office` (das v9.393.1 auf Temp+os.replace umgestellt wurde).

**FIX-RICHTUNG (Agent evaluiert):** (a) Walker robust gegen transiente Read-Fehler, (b) Deanon EINMAL pro Datei NACH allen Writes, (c) `_xlsx_reverse` atomar wie `_walk_office`, (d) Deanon gegen concurrent writes serialisieren. Garantie nötig: jede gelieferte Datei korrekt deanonymisiert, nie Fakes, nie korrupt.

Gehört zu [[project_pii_quality_test_plan]] (blockiert saubere Anon-Tests). NEBENBEFUND: Harness maß falsche von 3 Ausgabedateien (nahm erste alphabetisch statt vollständigste) — separat zu fixen.
