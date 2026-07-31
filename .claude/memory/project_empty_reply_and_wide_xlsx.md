---
name: project_empty_reply_and_wide_xlsx
description: v9.261.0 fixes — empty-reply-after-tools dropped tool-calls; XLSX max_col=16384 placeholder columns exploded read_document
metadata: 
  node_type: memory
  type: project
  originSessionId: c222787c-f141-4bdf-b86f-396ce1dfcc1f
---

v9.261.0 (2026-07-02, commit a8adf870) — zwei Bugs aus Chat c8ff6c66 ("did not finish"; Nutzer sah Antwort+Tool-Calls live, nach Reload weg).

**Diagnose-Beleg**: Server-Log-Zeile `[inprocess-loop] turn=… reply=0c rounds=2 tools=2 error=None cancelled=False` = Turn lief SAUBER, aber finale Textantwort war 0 Zeichen (`reply=0c`). Kein Cancel/Crash/Restart. DB hatte nur die User-Message, `active_turns` leer. Diese Log-Zeile ist der schnellste Weg, "Turn produzierte nie etwas" von "Turn lief, gab aber leer zurück" zu unterscheiden.

**Bug A — Datenverlust (handlers/chat.py Worker, ~Zeile 3714)**: Bei leerer Antwort lief IMMER `_rollback_messages(session, sid, _msg_count_before)` → verwarf ALLE Zwischennachrichten inkl. der live gezeigten Tool-Calls. Fix: Empty-Reply-Zweig rettet jetzt bei vorhandenen `_partial_tools` eine partielle Assistant-Message (Marker "Keine Textantwort — Werkzeuge ausgeführt, keine Antwort formuliert. Bitte erneut senden." + tools-Meta + Usage) — dasselbe Prinzip wie die Cancel/Error-Zweige (das 1fa62d2d-Prinzip: nichts Sichtbares still fallenlassen). Nur ein WIRKLICH leerer Turn (kein Text UND keine Tools) rollt noch zurück.

**Bug B — Ursache: XLSX-Extraktion (engine/doc_convert._extract_xlsx)**: Sheet meldete `max_column=16384` (Excel-Blattlimit) — nach ~37 echten Spalten folgten 16000+ auto-benannte Platzhalter-Header `Spalte41`…`Spalte16347` OHNE Daten (Excel-Artefakt bei Formatierung/Kopieren über das Blattende). Der bestehende Trailing-EMPTY-Trim griff NICHT (Header sind benannt, nicht leer) → flache Tabelle 1,5 MB, jeder Downstream-Pass kroch ~60s über 16k Spalten → überflutete das Modell, das leer abbrach. Fix: VOR allen per-Spalten-Schritten (Footer-Group-Scan etc.) auf `max(letzte-Spalte-mit-DATEN, letzter-contiguous-Nicht-`Spalte<N>`-Header)` beschneiden, Header+Zeilen slicen. Real: 60s/1.521.978 B → 0,30s/12.763 B. `_detect_footer_groups`/Table-Render laufen dann nur über echte Spalten. Muster-Erkennung: `re.fullmatch(r"Spalte\d+", header_cell)` = Excel-Auto-Platzhalter.

**ks-xlsx-parser (knowledgestack/excel-parser, MIT)**: vom Nutzer vorgeschlagen, geprüft, NICHT übernommen — baut selbst auf openpyxl, adressiert das 16k-Platzhalter-Problem nicht explizit, brächte neue Deps (pydantic/lxml/xxhash/tiktoken) gegen die dependency-arme Server-Linie (Brain läuft auf nacktem Homebrew-python3). Bietet sonst LLM-ready JSON + Citations + ZIP-Bomb-Guard — als Referenz für später notiert.

Kein kuratierter Changelog (interne Robustheit). Siehe [[project_inprocess_openai_loop]] (worker persist/rollback), doc_convert single-choke-point in engine/CLAUDE.md.
