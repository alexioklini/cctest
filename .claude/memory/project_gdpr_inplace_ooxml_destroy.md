---
name: project_gdpr_inplace_ooxml_destroy
description: "v9.393.1 — In-Place-Deanonymise zerstörte docx/pptx (47KB→22B); _walk_office öffnete dst mode='w' und trunkierte src VOR lazy zin.read; Fix Temp+os.replace"
metadata: 
  node_type: memory
  type: project
  originSessionId: c6e709c4-57da-4ab6-ad27-2a3b80e661ae
  modified: 2026-07-21T16:12:57.076Z
---

**Bug (Chat 3811cb61):** GDPR-After-File-Write-Callback ruft `deanonymize_file(path, path)` (src==dst, in-place). In `engine/file_pseudonymize._walk_office` (docx+pptx Reverse-Walker) öffnete die Re-Zip-Phase `dst_path` mit `zipfile.ZipFile(dst_path, 'w', …)` in DERSELBEN with-Zeile wie `src_path` mode 'r'. Python wertet Context-Manager links→rechts aus → mode='w' TRUNKIERT die Quelldatei auf 0 Byte, BEVOR die lazy `zin.read(name)`-Schleife die Member liest → 'Truncated file header', Re-Zip bricht ab, zurück bleibt 22-Byte-EOCD-Stub (`namelist()==[]`). Ein 47-KB-.docx (korrekt von write_document/doc.save erzeugt, size:47040 im Tool-Ergebnis) wurde beim Deanon-Rückschreiben vernichtet; in Word nicht öffenbar.

**Diagnose-Lektion:** NICHT vorschnell "falsches Tool / write_file" schließen — der Tool-Aufruf zeigte `write_document status:written size:47040`. Die Datei war beim Schreiben gültig, erst der POST-Write-Hook killte sie. `doc.save()` kann strukturell nie 22 Byte liefern. git-stash-Gegenprobe bewies: pre-fix 36765→22 Byte + FilePseudonymizeError, post-fix 36815→36618 + 50 Werte reversed + 17 Zip-Member intakt.

**Fix (Commit 1aaf6263):** In Geschwister-Temp `dst+'.pii-tmp'` schreiben, dann `os.replace(tmp, dst)` atomar; bei Fehler Temp entfernen, Original-dst unangetastet. Nur docx+pptx betroffen (`_walk_office`, lazy read). `_xlsx_reverse` (openpyxl lädt in RAM vor `wb.save`), `_plain_reverse`/`_csv_reverse` (lesen src komplett vor open(dst)) waren nie gefährdet. Tests: tests/test_deanonymize_file_inplace.py.

**OFFEN (Nebenbefund, nicht gefixt):** Das Modell meldete positive Datei-Prüfung, ohne die geschriebene Datei zurückzulesen — `moa_verify` prüft nur Antworttext gegen Plan, nicht Artefakt-Integrität. Fail-loud-Schutz wäre eine Post-Write-OOXML-Integritätsprüfung (zipfile öffnen + non-empty + word/document.xml vorhanden) in `write_document`/`_after_file_write`, die dem Modell einen Tool-Fehler zurückgibt statt "written, size 22". Siehe [[project_gdpr_one_scan_per_turn]], [[project_gdpr_tool_deanon_display]].
