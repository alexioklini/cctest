---
name: project_data_diff_wave
description: "v9.318.0 Daten-Diff-Welle: JSON/XML in Grid-Pipeline (xlsx-Tools format-agnostisch, Cross-Format-Diff), text_diff-Tool, Diff-Tab (MergeView) + Ein-Fenster-Modus (3× nachgeschärft: final max 3 Tabs je Slot)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 3a268ed5-c5fe-4842-b66a-3d7f1cf7b4ee
---

v9.318.0 (2026-07-13, Commit fd1a481b): **Daten-Diff-Welle** SHIPPED — Anlass: User-Audit "Diff/Merge für csv/excel/xml/json/code fehlt". Befund vorab: `xlsx_diff` existierte schon (v9.263/264) und deckte xlsx/csv ab; echte Lücken waren JSON/XML, Text/Code-Diff und UI-Visualisierung.

- **Grid-Pipeline v5**: `_load_grids` (xlsx_tools.py) lädt `.json/.jsonl/.ndjson/.xml` als Record-Grids → alle 5 xlsx-Tools format-agnostisch, Cross-Format-Diff (CSV↔JSON) und JSON/XML→Workbook gratis. Mapping: Record-Array=Tabelle (nested→`a.b`-Spalten, Lookup-Dict `{id:{...}}`→`_key`-Spalte, Skalar-Reste→`<stem>_meta`); XML: wiederholtes Element (≥2 Geschwister)=Tabelle, Text CSV-koerziert, JSON bleibt typisiert. GOTCHA: `_sanitize_name` strippt führende Unterstriche — `_key` heißt in SQL `key`.
- **text_diff** (engine/tools/diff_tools.py, documents): difflib unified + Zähler, `mode='json'` strukturell (Key-Reihenfolge egal, Array-Position zählt), `out='diff.html'` (HtmlDiff side-by-side). Binär-Guard verweist auf xlsx_diff. `compare='formulas'/'formats'` in xlsx_diff verweigert Nicht-XLSX jetzt sauber (crashte vorher schon auf CSV in `_sig_matrices`).
- **Diff-Tab** (kind:'diff', panels_terminal.js) via CodeMirror MergeView; Endpoint `GET /v1/files/file-diff` (path_a/path_b oder path+git=head). GOTCHA: merge.js braucht das **klassische diff_match_patch 20121119-CDN-Build** — npm-shaped Builds definieren die erwarteten Globals (diff_match_patch/DIFF_*) NICHT. Einstieg: Baum-Kontextmenü 'Diff gegen HEAD' (nur git-modifizierte Rows) + 'Zum Vergleich markieren'/'Vergleichen mit …' (`_wdDiffMark`).
- **Ein-Fenster-Modus** ersetzt Ein-Editor-Modus — Anforderung wurde VIERMAL live nachgeschärft: 'nur 1 Fenster' → 'nur 1 Tab' → 'max 3 Tabs, einer je Typ' → final (9.319.0) **'1 Tab je Typ, aber Pane-Layout/Splits FREI'** (die Normalize-auf-'a'-Sperre wurde wieder entfernt). Slot-Map `_SW_SLOT`: Editor-Slot=editor+diff, Terminal-Slot, Chat-Slot=chat+Subagenten-Hub; Enforcement nur noch via `_terminalSingleWindowClear(kind)` in jeder Tab-Erzeugung. Terminals werden DETACHT statt geschlossen (terminalCloseTab POSTet /close = PTY-Kill!); passiver Hub-Reattach verdrängt nie den Chat-Tab (unterdrückt). Persistiert `single_window`, liest legacy `single_editor`. LESSON: solche UI-Modi live iterieren lassen — nicht zu früh Semantik festschreiben.
- **9.319.0 (Commit c62be325), gleiche Session**: (a) Termchat scrollt beim Öffnen ans Ende — Root-Cause: `tcLoadTranscript` scrollte, aber VERDECKTE Tabs haben scrollHeight 0 → Fix im chat-Zweig von `_terminalActivate` (nur bei `_stick !== false`). (b) Chat-Tab-Menü 'Alles Chat-Fremde schließen' (`terminalCloseUnrelated`); Zugehörigkeit = Pfad in `chats/<slug>_<datum>_<sid>/` (`_terminalChatSidFromPath`, sid = Suffix nach letztem `_`). (c) **Auto-Close-Modus** (⚡-Toggle, `bottom_workspace.auto_close`): Chat-Auswahl (`tcOpenHistory`) oder Öffnen einer Chat-Datei (`terminalOpenFile` leitet sid aus Pfad ab) schließt chat-fremde Tabs automatisch — Auto-Pfad detacht Terminals, die Menü-Aktion schließt echt. net-globals final 1974.
- UI-Wünsche CSV-als-Grid + JSON/XML-Klappbaum existierten BEREITS (panels_terminal.js:1962 / `_terminalPaintTree`, Container >30 starten eingeklappt) — nichts gebaut.
- net-globals 1965→1969 (terminalOpenDiff, _terminalSingleWindowClear, _wdDiffMark, _SW_SLOT). 65 Tests, js_gate PASS, Endpoint live verifiziert (brain.py HEAD↔Worktree).

Verwandt: [[project_xlsx_toolset]], [[project_codemode_terminal]], [[feedback_single_fix_point]].
