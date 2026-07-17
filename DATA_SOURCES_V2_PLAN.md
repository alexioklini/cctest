# Datenquellen v2 — MSSQL, Read/Write-Modus, Projekt-/Chat-Scoping — Umsetzungsplan

**Stand:** 2026-07-17, Basis v9.363.0. Erarbeitet in der Session „Datenquellen-Admin-GUI"
(Commit d841397a) — dieser Plan setzt DIREKT auf der dort gebauten Welle auf und ist für
die Umsetzung in einer frischen Session geschrieben.

**Ziel:** Externe SQL-Datenquellen werden ein erstklassiger, granular steuerbarer
Analyse-Baustein: MSSQL zusätzlich zu Postgres; Quellen sind pro Stück read-only ODER
read/write; das Admin-GUI legt die **prinzipielle** Konfiguration fest (WER überhaupt +
WAS existiert), die **Nutzung im Kontext** legen Projekt-Config bzw. Chat-Auswahl fest
(WO/WELCHE Quelle + optional WELCHE Tabellen) — analog Input-Folders/Web-URLs, aber
**ohne Mining**. Auswahl in 1–2 Klicks: Quelle(n) anhaken + optional Tabellen — fertig.

**User-Entscheidungen (2026-07-17, verbindlich):**
1. **MSSQL** muss zusätzlich zu Postgres als Quellen-Typ anlegbar sein.
2. **Projekt-Scoping**: Admin-GUI = prinzipielle Konfig; ob eine Quelle in einem
   Projekt-Chat aktiv nutzbar ist, entscheidet die **Projekt-Config** — analog
   ingest folders/files, nur ohne Mining. Gilt für normale UND Code-Mode-Projekte.
3. **Projektlose Chats**: Quellen werden analog den Web-URLs **über das Right Panel**
   eingebunden (per Session).
4. **1–2 Klicks**: Datenbank(en) auswählen + optional auf Tabellen einschränken — fertig.
5. **Read-only vs. Read/Write pro Quelle** im Admin-GUI definiert — die abgesetzten
   SQL-Kommandos müssen sich danach richten.

---

## Verifizierte Ist-Fakten (Stand v9.363.0 — nicht erneut prüfen, außer bei Zweifel)

| Fakt | Beleg |
|---|---|
| `db_query`-Impl + 3-Schichten-Read-only (Prefix-Check, `set_session(readonly=True)`, RO-Grant) | `engine/tools/data_tools.py` (`tool_db_query`, `_connect_readonly` ~Z.330, `_check_select_only` shared mit xlsx/data_query — Single-Fix-Point) |
| Zugriffs-Policy WER (v9.363.0): `data_sources_access {enabled, roles, teams, users}`, Guard IM Tool (`data_access_allowed`), fehlender Block = nur Admins, KEINE Tool-Listen-Mutation (KV-Prefix) | `engine/tools/data_tools.py` (Guard ganz oben in `tool_db_query`), Tests `tests/test_data_tools.py::TestDbQueryAccessPolicy` (8) |
| Admin-Endpoints + GUI: `GET/POST /v1/data-sources` (DSN maskiert, Secret bleibt bei Edit, live ohne Restart), Tab „Datenquellen" | `handlers/admin_config.py` (`_handle_data_sources_get/post`), `web/js/settings_general_tabs.js` (`_genTab_data_sources` + 4 Handler), Gates `handlers/auth.py` `_ADMIN_GET_EXACT`/`_ADMIN_POST_EXACT` |
| Boot-Copy: `data_sources` + `data_sources_access` aus config.json | `server.py` main() ~Z.3766 |
| Typ-Gate: nur `postgres` verdrahtet, andere fail-loud; GUI-Typ-Dropdown liest `wired_types` aus dem GET | `data_tools.py` `_connect_readonly`; `_genTab_data_sources` |
| `sqlglot 30.12.0` IST im Server-Python (Homebrew py3.14) | live verifiziert 2026-07-17 |
| `pymssql` FEHLT im Server-Python | live verifiziert 2026-07-17 |
| Projekt-Feld-Whitelist (`web_urls` etc.) — neue project.json-Felder MÜSSEN hier rein | `brain.py:7357` (`ProjectManager.update_project`, Whitelist-Loop ~Z.7377) |
| `code_mode` ist NICHT editierbar (fix bei Anlage) — Code-Projekte sind normale Projekte mit Flag; eine neue Settings-Sektion rendert in beiden | `brain.py` update_project NOTE |
| Web-URLs-Editor als Muster für die Projekt-Sektion | `web/js/panels_projects.js:2076` ff. (speichert via `API.updateProject(..., {web_urls})`) |
| Per-Session-Persistenz-Muster (Websuche-Basket): Spalte `sessions.web_basket`, manage action `web_basket`, Right-Panel-Tab | `handlers/sessions_handler.py:2180` (action), `:692/:726` (GET), `web/js/panels_websuche.js`, Right-Panel-Tabs `web/index.html:1764` ff. |
| `db_query` ist in `GDPR_ARGS_DEANON_TOOLS` (SQL läuft auf Realwerten), NICHT in `_WORKFLOW_STEP_TOOLS` (deny-by-default) | v9.356.0-Changelog, `brain.py` |
| Test-Muster: `tests/test_data_tools.py` — Postgres-Suite skippt sauber ohne lokale `braintest`-DB; Policy-Suite mockt AuthDB | heute erweitert (30 Tests grün) |
| Lokale Test-DB Postgres: `braintest` (50k `positionen`, Role `brain_ro`), DSN in config.json | `config.json → data_sources[braintest]` |
| Buttons = inline SVG, NIE Emoji | [[feedback_svg_not_emoji_buttons]] |
| Kern-Mechanik validieren VOR UI/Config | [[feedback_phase_a_then_validate]] |

---

## Architektur-Entscheidungen (mit Begründung)

**E1 — Zwei orthogonale Achsen, beide müssen passieren.** Die 9.363.0-Policy
(`data_sources_access`) bleibt unverändert die **WER**-Achse (global/Rolle/Team/User).
NEU kommt die **WAS/WO**-Achse: ein per-Turn-Scope (welche Quellen + Tabellen in DIESEM
Kontext). Enforcement bleibt **im Tool** (kein exclude_tools, KV-Prefix bleibt
byte-stabil — das 9.363.0-Muster). Reihenfolge im Guard: Policy (WER) → Scope (WAS) →
Modus (ro/rw) → Tabellen.

**E2 — Scope-Quelle je Kontext:**
- **Projekt-Chat**: `project.json → data_sources: [{name, tables: []}]`. Nur gelistete
  Quellen sind nutzbar; **fehlend/leer = KEINE Quelle nutzbar im Projekt** (die
  Projekt-Config LEGT die Nutzung fest — Anforderung 2; kein stilles Global-Fallback).
  `tables: []` = alle Tabellen der Quelle.
- **Projektloser Chat**: Session-Auswahl über das Right Panel,
  `sessions.data_sources` (JSON, gleiche Shape), manage action analog `web_basket`.
  Nichts ausgewählt = db_query verweigert mit Hinweis „Quellen im rechten Panel wählen".
- **Kein Mining**: Der Scope ist reine Laufzeit-Berechtigung. project-sync,
  MemPalace, KG fassen Datenquellen NICht an (explizites Nicht-Ziel).

**E3 — MSSQL via `pymssql`** (pip-Wheel mit eingebettetem FreeTDS; `pyodbc` bräuchte
die msodbcsql18-Systeminstallation — vermeiden). DSN bleibt EIN URL-Feld in der GUI
(`mssql://user:pass@host:1433/db`), Brain parst selbst (`urllib.parse`). Timeouts:
`pymssql.connect(login_timeout=connect_timeout, timeout=statement_timeout)` — das
`timeout`-Argument ist der Query-Timeout (Äquivalent zum Postgres-`statement_timeout`).

**E4 — Read-only auf MSSQL ist ehrlich ZWEI-schichtig.** MSSQL hat KEIN Session-Äquivalent
zu `set_session(readonly=True)` (Read-only gibt es nur DB-weit oder über Grants). Für
`mssql`+`ro` tragen Schicht 1 (Statement-Gate) + Schicht 3 (Login nur mit `db_datareader`).
Das `read_only`-Feld im Tool-Ergebnis und das Rezept müssen das EHRLICH sagen (kein
kopiertes „session read-only"). Rezept-SQL: `CREATE LOGIN … ; CREATE USER … ;
ALTER ROLE db_datareader ADD MEMBER …`.

**E5 — `access_mode: "ro" | "rw"` pro Quelle, Default `ro`.**
- `ro`: exakt heutiges Verhalten (`_check_select_only`; Postgres zusätzlich Session-RO).
- `rw`: Ein-Statement-Zwang und Multi-Statement-Reject BLEIBEN; zugelassen werden
  SELECT/WITH/INSERT/UPDATE/DELETE/MERGE. **DDL (CREATE/ALTER/DROP/TRUNCATE/GRANT)
  bleibt auch auf rw-Quellen geblockt** — Schema-Änderungen durch den Agenten sind
  ein anderes Risiko-Level; wer sie braucht, gibt sie bewusst später frei (offener
  Punkt O3). Letzte Instanz bleiben die DB-Grants des hinterlegten Users.
  rw-Postgres: KEIN `set_session(readonly=True)`, expliziter `conn.commit()`,
  Ergebnis meldet `rowcount` + `mode:"rw"`.
- Neuer geteilter Checker `_check_statement_allowed(sql, mode)` NEBEN
  `_check_select_only` (der bleibt unverändert für xlsx_query/data_query — deren
  ro-Semantik ist nicht konfigurierbar).
- Schema-Beschreibung db_query: Modus kommt aus der Quelle; Write auf ro-Quelle →
  Fehlertext nennt den Modus („source is read-only — writes need an rw source").

**E6 — Tabellen-Restriktion hart via sqlglot** (ist installiert, 30.12.0):
`sqlglot.parse_one(sql, dialect='postgres'|'tsql')`, alle `exp.Table`-Refs extrahieren,
case-insensitiv gegen die Whitelist (Namen normalisiert: `schema.table` und nacktes
`table` matchen beide). `information_schema.*` (und mssql `sys.*`) bleiben IMMER
erlaubt — Schema-Exploration ist der dokumentierte Arbeitsweg; dass Metadaten
nicht-gelisteter Tabellen sichtbar bleiben, ist eine bewusste, zu dokumentierende
Grenze (O2). **Unparsebares SQL → fail-closed** (Fehler nennt die erlaubten Tabellen).
CTE-Namen dürfen nicht als Tabellen-Refs zählen (sqlglot unterscheidet das; Test dafür).

**E7 — Neue Endpoints (nicht admin-only, aber policy-gated):**
- `GET /v1/data-sources/available` — für Right Panel + Projekt-Settings: Quellen
  gefiltert auf `data_access_allowed(user)`, NUR `{name, type, access_mode}` — NIE
  DSN/env_key. (Der bestehende admin-GET bleibt unverändert admin-only.)
- `GET /v1/data-sources/<name>/tables` — Tabellenliste für den Picker
  (`information_schema.tables` bzw. mssql-Äquivalent), policy-gated, Timeout kurz
  (5 s), Fehler sauber (Quelle offline → Fehlertext, kein 500).
- Beide in server.py-GET-Dispatch registrieren; NICHT in `_ADMIN_GET_EXACT`
  (Handler prüft selbst via `data_access_allowed` + `_require_auth`).

**E8 — Worker-Verdrahtung (ein Choke-Point):** `handlers/chat.py` setzt vor `run_turn`
`get_request_context().data_source_scope` (neues RequestContext-Feld,
`engine/context.py`): aus `project.json → data_sources` wenn die Session ein Projekt
hat, sonst aus `sessions.data_sources`. Shape: `{name: [tables] | []}` ([] = alle).
`None`/fehlend = kein Scope gesetzt → Tool verweigert (außer `__system__`).
Hintergrund-Turns: `sidecar_proxy._apply_bg_context` reicht das Feld durch, wenn die
ExecutionContext eine Projekt-Session trägt (Scheduler-Läufe IN einem Projekt erben
dessen Scope); sched-Sessions OHNE Projekt → kein Scope → deny (O1).

**E9 — GUI-Änderungen minimal-invasiv:**
- Admin-Tab „Datenquellen": Typ-Dropdown bekommt `mssql` automatisch über
  `wired_types` (KEINE GUI-Änderung nötig); NEU nur Radio/Select `access_mode`
  (ro Default, rw mit Warntext) im Quellen-Formular + Badge in der Quellen-Liste.
- Projekt-Settings: neue Sektion „Datenquellen" in `panels_projects.js` DIREKT nach
  dem Web-URLs-Muster (Z.2076 ff.): Checkbox je verfügbarer Quelle (aus
  `/v1/data-sources/available`), pro angehakter Quelle aufklappbare Tabellen-Chips
  (lazy via tables-Endpoint, Multi-Select, leer = alle). Speichern via
  `API.updateProject(..., {data_sources})`. Rendert in normalen UND
  Code-Mode-Projekten (gleiche Settings-Ansicht).
- Right Panel: neuer Tab „Datenquellen" (inline-SVG-Icon, Datenbank-Zylinder) analog
  Websuche: Liste verfügbarer Quellen mit Checkbox + Tabellen-Picker, Persistenz
  per manage action `data_sources` (Muster `web_basket`,
  `sessions_handler.py:2180`). Tab nur sichtbar/gefüllt, wenn
  `/v1/data-sources/available` nicht leer (sonst Hinweis-Text). In Projekt-Sessions
  zeigt der Tab READ-ONLY den Projekt-Scope an („im Projekt konfiguriert") statt
  eigener Auswahl — eine Quelle der Wahrheit pro Kontext.

---

## Phasen (Reihenfolge = Risiko zuerst, Kern vor UI — [[feedback_phase_a_then_validate]])

### Phase 1 — MSSQL-Treiber + Test-Infrastruktur
1. Test-MSSQL beschaffen: Docker (`mcr.microsoft.com/mssql/server:2022-latest`;
   auf Apple Silicon mit `--platform linux/amd64` unter Rosetta/colima — ERST prüfen
   ob Docker/colima auf der Maschine läuft; falls nein: User fragen bevor etwas
   installiert wird). Test-DB `braintest_ms` mit denselben 50k `positionen` +
   Read-only-Login `brain_ro_ms` (db_datareader) + Owner-Login für den
   Schicht-Beweis. DSN in config.json (gitignored; Scrubber deckt `dsn` schon ab).
2. `pymssql` ins Server-Python (`pip3 install pymssql --break-system-packages`,
   Versionsstand im Changelog festhalten).
3. `data_tools.py`: `_connect_readonly` → um `mssql`-Branch erweitern (DSN-Parse,
   login_timeout/timeout aus options, Cursor OHNE named-cursor — pymssql streamt
   via `fetchmany` ohnehin; Ergebnis-Kappe identisch). fail-loud-Text für weitere
   Typen (snowflake/oracle) bleibt.
4. Tests (skip-clean ohne lokale MSSQL, das braintest-Muster): SELECT liefert,
   INSERT stirbt an Schicht 1, falscher Quellname listet, tote Verbindung sauber,
   `out=csv`. **Erfolgskriterium:** komplette Suite grün MIT laufendem Docker-MSSQL;
   ohne Docker skippt sie sichtbar (nicht still grün).

### Phase 2 — access_mode ro/rw
1. Quellen-Feld `access_mode` (`ro` Default; GET liefert es, save_source validiert
   `ro|rw`). Admin-GUI: Auswahl im Formular + Badge in der Liste (rw farblich
   abgesetzt, Warn-Hinweis „Schreibzugriff — DB-Grants sind die letzte Instanz").
2. `_check_statement_allowed(sql, mode)` in data_tools (ro = heutige Prüfung;
   rw = +INSERT/UPDATE/DELETE/MERGE, DDL-Blocklist, Ein-Statement-Zwang bleibt).
   `tool_db_query`: Modus-Zweig — rw-Postgres ohne Session-RO + commit; mssql-ro
   ehrliches `read_only`-Feld (E4).
3. Schema-Beschreibung db_query aktualisieren (Modus, Fehlersemantik, „access
   denied ist final").
4. Tests: rw-Postgres INSERT+SELECT-Roundtrip auf einer Wegwerf-Tabelle in
   braintest (Owner-DSN als rw-Quelle), DDL auf rw geblockt, Write auf ro-Quelle
   geblockt mit Modus-Fehlertext, mssql-ro-Suite aus Phase 1 unverändert grün.
   **Erfolgskriterium:** Live-Chat-Test — Agent schreibt auf rw-Quelle eine Zeile
   und liest sie zurück; dieselbe Anweisung auf ro-Quelle wird sauber verweigert.

### Phase 3 — Scoping-Kern (Context + Tool-Gate + Tabellen)
1. `RequestContext.data_source_scope` (engine/context.py, Default None).
2. Guard-Erweiterung in `tool_db_query`: nach Policy-Check → Scope-Check
   (Quelle im Scope? sonst Fehlertext mit Hinweis auf Projekt-Settings/Right Panel)
   → Tabellen-Check via sqlglot (E6). `__system__` behält Vollzugriff.
3. Tests (reine Unit, gemockter Scope): Quelle nicht im Scope, Tabellen-Whitelist
   positiv/negativ (inkl. schema-qualifiziert, CTE-Name zählt nicht als Tabelle,
   information_schema immer erlaubt, unparsebares SQL fail-closed), Scope None =
   deny für normale User. **Erfolgskriterium:** alle Kombinationen unit-grün,
   BEVOR irgendein UI existiert.

### Phase 4 — Projekt-Config (normale + Code-Mode-Projekte)
1. `project.json → data_sources` in die update_project-Whitelist (`brain.py:7377`,
   Validierung: Liste von `{name, tables}` gegen konfigurierte Quellen).
2. Endpoints E7 (`available` + `tables`) implementieren + registrieren.
3. `handlers/chat.py`: Scope-Setzung aus dem Projekt (E8); `_apply_bg_context`
   für Projekt-Scheduler-Läufe.
4. Projekt-Settings-Sektion (panels_projects.js, Web-URLs-Muster) — Checkboxen +
   Tabellen-Chips, 1–2 Klicks (Anforderung 4).
5. js_gate (Baseline-Bump für neue Globals dokumentieren).
   **Erfolgskriterium (live, Browser):** Projekt A bekommt Quelle braintest mit
   `tables:[positionen]` → Chat im Projekt beantwortet Aggregat; Query auf eine
   andere Tabelle wird mit Whitelist-Fehler verweigert; Projekt B ohne
   Datenquellen → db_query verweigert; dasselbe in einem Code-Mode-Projekt.

### Phase 5 — Projektlose Chats (Right Panel)
1. Spalte `sessions.data_sources` (additive Migration, web_basket-Muster) +
   manage action + GET /messages liefert sie mit.
2. Right-Panel-Tab (E9): Auswahl-UI, Persistenz, Projekt-Session = read-only-Anzeige.
3. `handlers/chat.py`: Scope aus Session-Auswahl, wenn kein Projekt.
4. js_gate. **Erfolgskriterium (live, Browser):** frischer projektloser Chat →
   db_query verweigert mit Hinweis; 2 Klicks im Panel (Quelle anhaken) → dieselbe
   Frage läuft; Tabellen-Einschränkung im Panel greift im nächsten Turn;
   Auswahl überlebt Reload (Session-persistiert, nicht localStorage).

### Phase 6 — Docs + Release
- Skill: 01-api (3 neue Endpoints + Felder), 02-tools (Modus + Scope im
  db_query-Block), 04-recipes (Datenanbindung: MSSQL-Rezept inkl. db_datareader,
  rw-Warnung, Projekt-/Chat-Scoping-Anleitung), 06-user-manual (DE: Admin-Tab-Update,
  Projekt-Sektion, Right-Panel-Tab), 05-internals (Scope-Mechanik), SKILL.md-Bump.
- Kuratierte Einträge: einer `admin` (MSSQL + rw + Scoping-Verwaltung), einer
  `user` (Datenquellen im Chat/Projekt in 2 Klicks nutzen).
- VERSION + CHANGELOG je Phase-Commit ([[feedback_version_two_places]]);
  Server-Restart je Welle; Test-Sessions löschen ([[feedback_cleanup_test_sessions]]).

---

## Explizit NICHT im Scope
- Kein Mining/MemPalace/KG über Datenquellen (reine Laufzeit-Berechtigung).
- Kein Snowflake/Oracle (weiter fail-loud; ein Branch, sobald testbarer DSN existiert).
- Keine per-Schedule-Datenquellen-Auswahl (O1, Default deny).
- Kein DDL auf rw-Quellen (O3).
- Keine Zeilen-/Spalten-Ebene der Restriktion (nur Tabellen-Whitelist).

## Offene Punkte (Defaults, in der Umsetzung bestätigen)
- **O1 — sched-Sessions ohne Projekt:** Default deny (kein Scope). Wenn ein realer
  Scheduler-Use-Case auftaucht → per-Schedule-Feld analog attachments.
- **O2 — information_schema unter Tabellen-Restriktion:** bleibt offen lesbar
  (Exploration nötig); Metadaten-Sichtbarkeit nicht-gelisteter Tabellen ist
  dokumentierte Grenze. Alternative (Filter auf die Whitelist) nur bei Bedarf.
- **O3 — DDL auf rw:** geblockt. Freigabe wäre ein drittes access_mode-Level
  („admin"), erst bei echtem Bedarf.
- **O4 — MSSQL-Docker auf Apple Silicon:** Rosetta-Emulation kann zäh sein; wenn
  unbrauchbar, Alternative: echter MSSQL des Users (DSN liefern lassen) — Phase 1
  NICHT mit ungetestetem Treiber-Code abschließen ([[feedback_phase_a_then_validate]]).
