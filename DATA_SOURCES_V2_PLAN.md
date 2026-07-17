# Datenquellen v2 — MSSQL, REST, Read/Write-Modus, Projekt-/Chat-Scoping — Umsetzungsplan

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
6. **REST-APIs als zweite Quellen-Klasse** (Nachtrag gleicher Tag): generelle
   Abfragemöglichkeit strukturierter externer Daten (SQL **und** REST) via Chat —
   gleiche Policy-, Scoping- und Auswahl-Mechanik für beide Klassen.
7. **Konkreter Anwendungsfall: Hyland OnBase** (Nachtrag gleicher Tag) — siehe
   Anhang A; treibt MSSQL (Phase 1) + Tabellen-Whitelist (Phase 3) + REST (Phase 6).
8. **Quellen-Steckbrief** (Nachtrag gleicher Tag): pro Datenquelle hinterlegbares
   Nutzungswissen (Tabellen-/Feld-Semantik, Join-Pfade, REST-Endpoint-Verwendung,
   korrekte Persistier-Muster) — „ähnlich wie ein Skill oder MCP-Server, den man
   zusätzlich bei der Konfig angibt" — damit das Modell NICHT bei jeder Nutzung
   das Schema neu ermitteln muss, sondern beim Ansprechen der Quelle schon weiß,
   wie es zu den Daten kommt bzw. korrekt schreibt.
9. **Maximale lokale Auslagerung** (Nachtrag gleicher Tag): Verarbeitung wie beim
   xlsx-/OCR-Toolset deterministisch auf dem Server — ein LLM sieht Rohdaten nur
   in zwingenden Fällen. DSGVO-Begründung: bei großen Datenmengen ist
   Anonymisierung/Deanonymisierung bzw. Ausweichen auf ein lokales Modell nicht
   immer möglich oder sinnvoll — der bessere Schutz ist, dass Massendaten den
   LLM-Kontext GAR NICHT erreichen.
10. **Bank-verifizierter MSSQL-Verbindungsweg** (Nachtrag 2026-07-17): `pyodbc` +
    „ODBC Driver 17 for SQL Server" ist der EINZIGE Weg, der im Netz der Bank
    nachweislich funktioniert (funktionierendes Specimen: `sync_service.py` eines
    bestehenden internen Tools; die Herleitung war aufwendig — Auszüge in
    Anhang B). E3 entsprechend REVIDIERT: pymssql/FreeTDS verworfen.

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
| `pymssql` FEHLT im Server-Python (inzwischen irrelevant — E3 nutzt pyodbc; ob `pyodbc` + msodbcsql17 installiert sind, ist Phase-1-Aufgabe) | live verifiziert 2026-07-17 |
| Bank-Specimen: pyodbc + „ODBC Driver 17 for SQL Server", `SERVER=host,port` (Komma), OHNE Encrypt-Params, Windows-Auth via `Trusted_Connection=yes`, Login-/Query-Timeout getrennt | Anhang B (aus `sync_service.py`, im Banknetz produktiv) |
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

**E3 — MSSQL via `pyodbc` + „ODBC Driver 17 for SQL Server"** (REVIDIERT 2026-07-17,
User-Entscheidung 10 — Konflikt-Auflösung: ursprünglich war `pymssql` geplant, um die
System-Treiber-Installation zu vermeiden; im Ziel-Banknetz ist aber pyodbc+Driver 17
der EINZIGE verifiziert funktionierende Weg (Specimen Anhang B). Der Convenience-Vorteil
von FreeTDS wiegt ein ungetestetes Protokoll-Stack-Risiko genau am kritischen
Einsatzort nicht auf — pymssql verworfen, nicht als Fallback behalten.)
- **Verbindungsstring EXAKT nach Specimen** bauen:
  `DRIVER={ODBC Driver 17 for SQL Server};SERVER=host,port;DATABASE=db;UID=…;PWD=…`
  — `SERVER=host,port` mit **KOMMA** (nicht `host:port`), und bewusst **OHNE**
  `Encrypt=`/`TrustServerCertificate=`: Driver 17 default `Encrypt=no` ist das, was
  im Banknetz funktioniert. NICHT auf Driver 18 „upgraden" (default `Encrypt=yes` →
  scheitert an on-prem-Servern mit Self-Signed-Zertifikaten).
- **Treibername konfigurierbar**: `options.odbc_driver` pro Quelle (Default
  „ODBC Driver 17 for SQL Server"; das Specimen löst dasselbe per env
  `MSSQL_DRIVER`) — deckt Maschinen ab, auf denen nur ein anderer Treiber liegt.
- **DSN bleibt EIN URL-Feld** in der GUI (`mssql://user:pass@host:1433/db`), Brain
  parst selbst (`urllib.parse`) und baut daraus den ODBC-String.
- **Windows Authentication** als Option (`options.windows_auth: true` → DSN ohne
  Credentials, `Trusted_Connection=yes;` statt UID/PWD — im Banknetz gängig, das
  Specimen unterstützt beide Wege). Caveat dokumentieren: vom Brain-Host aus nur
  nutzbar, wenn der Prozess Domain-/Kerberos-Kontext hat; SQL-Auth ist der Default.
- **Timeouts nach Specimen, ZWEI getrennte Knöpfe**:
  `pyodbc.connect(conn_str, timeout=connect_timeout)` ist der **Login**-Timeout;
  der **Query**-Timeout wird danach als `conn.timeout = statement_timeout` gesetzt
  (Specimen: 30/60; Test-Connection mit `timeout=5`). Das ist das Äquivalent zum
  Postgres-`statement_timeout`.
- **Installation**: `pip3 install pyodbc --break-system-packages` + msodbcsql17
  (macOS: Microsofts Homebrew-Tap `microsoft/mssql-release`, auf Apple Silicon
  verfügbar). Auf Windows-Zielsystemen der Bank ist Driver 17 typischerweise
  bereits installiert.

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
  (`information_schema.tables`; mssql: `INFORMATION_SCHEMA.TABLES WHERE
  TABLE_TYPE='BASE TABLE'` — bank-erprobt, Anhang B), policy-gated, Timeout kurz
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
2. `pyodbc` + Treiber ins Server-Python: `pip3 install pyodbc
   --break-system-packages` + msodbcsql17 via `brew tap microsoft/mssql-release`
   (Versionsstände im Changelog festhalten).
3. `data_tools.py`: `_connect_readonly` → um `mssql`-Branch erweitern (DSN-Parse →
   ODBC-String EXAKT nach E3/Anhang B; connect-`timeout` = Login-Timeout,
   `conn.timeout` = Query-Timeout aus options; `options.odbc_driver` +
   `options.windows_auth`; Cursor OHNE named-cursor — pyodbc streamt via
   `fetchmany`; Ergebnis-Kappe identisch). fail-loud-Text für weitere Typen
   (snowflake/oracle) bleibt.
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

### Phase 6 — REST-Quellen (zweite Quellen-Klasse, User-Entscheidung 6)

**E10 — REST-Quelle = admin-konfigurierte Base-URL, NIE freies Fetchen.** Das ist die
harte Abgrenzung zu `web_fetch`: `rest_query` erreicht AUSSCHLIESSLICH die vom Admin
hinterlegte `base_url` — Pfade werden angehängt und validiert (kein absolutes http…,
kein `..`, kein Schema-Wechsel). Dadurch ist SSRF strukturell ausgeschlossen und die
Quelle ist ein DATENPUNKT wie eine DB, kein Browser.

1. Quellen-Shape-Erweiterung (`data_sources`, gleiche Liste, GUI-Formular
   verzweigt nach Typ): `{name, type:"rest", base_url, auth:{kind: none|bearer|
   header|basic, secret|env_key, header_name?}, access_mode: ro|rw,
   allowed_paths?: ["/api/v1/…"], options:{timeout_s, max_response_kb}}`.
   Secret maskiert wie DSN (leer beim Edit = unverändert; Scrubber-Marker
   `secret` in scripts/scrub_config.py ergänzen — prüfen ob schon gedeckt).
2. NEUES TOOL `rest_query(source, path, method?, params?, body?, out?)`
   (4 Sites / 3 Dateien, Gruppe documents, `engine/tools/data_tools.py`):
   - Policy-Gate: DERSELBE `data_access_allowed` (WER-Achse gilt für alle
     Quellen-Klassen); Scope-Gate: DERSELBE `data_source_scope` (WAS-Achse) —
     statt Tabellen wirken **Pfad-Präfixe** als Ressourcen-Restriktion
     (Verallgemeinerung: SQL-Quelle → Tabellen, REST-Quelle → Pfade; eine
     Scope-Shape `{name: [ressourcen]}` für beide).
   - `access_mode`: ro = nur GET/HEAD; rw = +POST/PUT/PATCH/DELETE.
   - Ergebnis: JSON hübsch + gekappt (max_response_kb, Default 256), non-JSON
     als Text gekappt, `out='name.json|csv'` als Artefakt (JSON-Array →
     CSV-Flatten best effort), GDPR-Pass (`rest_query:<source>`),
     Fehler = sauberes Tool-Ergebnis (Timeout/4xx/5xx mit Body-Auszug).
   - httpx ist im Server-Python (der Loop nutzt es) — kein neues Dep.
3. GUI: Admin-Formular Typ-Zweig REST; Projekt-Sektion + Right Panel zeigen
   SQL- und REST-Quellen UNIFORM (Picker-Label „Tabellen" ↔ „Pfade").
   Tables-Endpoint-Analogon für REST: `allowed_paths` aus der Quellen-Config
   als Vorschlagsliste (KEIN Discovery-Call — REST hat kein information_schema).
4. Tests: Lokaler Stub-HTTP-Server im Test (stdlib, das render_service-Muster):
   ro blockt POST, Pfad-Whitelist positiv/negativ, Pfad-Escape (`..`, absolute
   URL) geblockt, Auth-Header gesetzt, Kappe greift, Timeout sauber.
   **Erfolgskriterium (live):** konfigurierte REST-Quelle (z. B. interne API)
   im Right Panel angehakt → Chat-Frage führt GET aus und zitiert Felder;
   POST auf ro-Quelle verweigert mit Modus-Fehlertext.
5. NICHT in `_WORKFLOW_STEP_TOOLS`, IN `GDPR_ARGS_DEANON_TOOLS` (wie db_query).

### Phase 7 — Quellen-Steckbrief (User-Entscheidung 8)

**E11 — Steckbrief = per-Quelle-Wissen, injiziert wie die Websuche-Preamble,
skaliert wie ein Skill.** Kein neuer Mechanismus-Typ, sondern die zwei bewährten
Auslieferungswege, nach Größe gewählt:

- **Quellen-Shape**: `guide: {md: str, skill?: str, auto_generated_at?: iso}` pro
  Datenquelle. `md` = admin-editierbares Markdown (Tabellen + Feld-Semantik,
  Join-Pfade, bewährte Beispiel-Queries, bei rw die korrekten Persistier-Muster
  wie „neue Sätze NUR in staging_x mit Spalten …"; bei REST: Endpoints, Parameter,
  Response-Shapes, Fehlersemantik). `skill` = alternativ/zusätzlich der Name eines
  bestehenden Agent-Skills (use_skill-Infra) für umfangreiche Dokumentation.
- **Auslieferung klein (≤ ~4k Tokens über alle gescopten Quellen)**: WIRE-ONLY
  Preamble auf der letzten User-Message, wenn die Quelle im Turn-Scope ist —
  exakt der Websuche-Seam (`handlers/chat.py:388
  _inject_web_preamble_into_wire`; Design-Context-Preamble als zweiter
  Präzedenzfall). History/DB bleiben sauber, nichts veraltet (jeder Turn liest
  den aktuellen Steckbrief), System-Prompt bleibt byte-stabil (KV-Prefix).
  Das Modell kennt Schema + Verwendung VOR dem ersten Tool-Call — null
  Erkundungs-Runden.
- **Auslieferung groß**: NICHT injizieren ([[feedback_prompt_bloat_regression]]) —
  die Preamble enthält dann nur eine Kurzzeile pro Quelle („Quelle X: lade
  use_skill('…') vor der ersten Abfrage"), das Wissen kommt lazy via use_skill.
  Grenze konfigurierbar (`data_sources_guide_max_tokens`, Default 4000).
- **Bootstrap statt Handarbeit**: Admin-GUI-Button „Steckbrief generieren" →
  `POST /v1/data-sources {action: generate_guide, name}`: liest
  information_schema (Tabellen, Spalten, Typen, FKs, row counts; MSSQL analog),
  optional ein LLM-Pass (`background_call`, cost_purpose getaggt) verdichtet zu
  Markdown mit Feld-Beschreibungs-Platzhaltern; Ergebnis landet editierbar in
  `guide.md` (+ `auto_generated_at`). Für REST optional: OpenAPI-URL angeben →
  fetch + verdichten (O7). Der Admin kuratiert danach — der Steckbrief ist
  HANDGEPFLEGT mit Auto-Anschub, wie brain-agent-guide.
- **GUI**: Steckbrief-Textarea im Quellen-Formular (Admin-Tab) + Generieren-Button;
  Projekt-Sektion/Right-Panel zeigen nur ein 📄-Indikator („Steckbrief vorhanden").

**E12 — Eskalationsleiter Quellen-Wissen (User-Präzisierung 2026-07-17).** Bei
komplexen Schemata (Referenzfall: die interne **Schatten-DB des Kernbankensystems**,
siehe CoreBanking-SQL-Showcase-Projekt) ist das Wissen PROZEDURAL, nicht deskriptiv —
„alle aktiven Kunden ermittelt man in Tabelle X, wobei Felder xy den Wert '1' haben
müssen" ist mit einer Feldbeschreibung nicht abgetan. Drei Stufen, v1 baut 1+2,
Stufe 3 ist der dokumentierte Pfad:

- **Stufe 1 — Steckbrief** (`guide.md`, oben): Tabellen-/Feld-Semantik, einfache
  Quellen. Reicht, „damit sich das Modell den Weg ebnet".
- **Stufe 2 — Quellen-Skill** (`guide.skill`, use_skill-Infra): vollwertige
  Wissensbasis nach dem brain-agent-guide-Muster (SKILL.md + Referenzdateien),
  Kern ist eine **Rezept-Bibliothek** „Geschäftsbegriff → verifizierte Query":
  aktive Kunden, Bestandsabgleich, Stichtags-Joins — je Rezept SQL/REST-Aufruf,
  Vorbedingungen, bekannte Fallen; bei rw die verbindlichen Persistier-Muster.
  Zwei Füllwege, damit das nicht Handarbeit bleibt:
  (a) **Seed aus bestehenden Zugriffsskripten** — die heutigen Skripte im
  CoreBanking-Showcase-Projekt kodieren das Wissen bereits; ein Bootstrap-Pass
  (background_call) extrahiert SQL + Kommentare in Rezeptform;
  (b) **Promotion aus Chats** — der bestehende Skill-Generator (v9.294,
  „SKILL.md aus Chat") befördert eine bewährte Query-Session per Klick in den
  Quellen-Skill: was sich das Modell einmal erarbeitet hat, wird kodifiziert
  statt im nächsten Chat neu ermittelt.
- **Stufe 3 — MCP-Server pro Quelle** (`mcp_server`-Feld, MCPManager +
  per-agent mcp.json existieren — Anbindung ist Konfig, kein Umbau): wenn Wissen
  zu VERHALTEN werden muss. Eskalationskriterien: (1) Rezepte sollen
  PARAMETRISIERT + deterministisch laufen statt vom Modell adaptiert
  (compliance-kritische Persistierung → typisiertes Tool
  `get_active_customers(stichtag)` statt SQL-Vorlage); (2) ein fertiger
  Hersteller-MCP existiert; (3) dieselben Rezepte werden außerhalb Brains
  (andere MCP-Clients) gebraucht. Ein selbstgeschriebener Quellen-MCP ist dann
  der dünne Wrapper über der Stufe-2-Rezept-Bibliothek — die Rezepte sind die
  Spezifikation. Stand 2026-07: für Hyland OnBase existiert KEIN dedizierter
  MCP-Server (Hyland baut „Agent Builder"/„Enterprise Agent Mesh" — beobachten,
  Anhang A).
- **Tests**: Preamble nur bei gescopter Quelle; Kappe → Skill-Hinweis statt
  Voll-Injektion; `session.messages`/DB tragen NIE Steckbrief-Text (wire-only,
  der v9.17.0-Regressionstest-Gedanke); generate_guide gegen braintest erzeugt
  Tabellen+Spalten-Markdown. **Erfolgskriterium (live, messbar):** dieselbe
  Join-Frage an braintest MIT Steckbrief = korrektes Ergebnis im ERSTEN
  db_query-Call (keine information_schema-Runde); OHNE Steckbrief braucht das
  Modell nachweislich Erkundungs-Runden. Bei rw: Persistier-Muster aus dem
  Steckbrief wird befolgt (INSERT landet in der dokumentierten Tabelle/Spalten).

### Phase 8 — Datensparsame Verarbeitungskette (User-Entscheidung 9)

**E13 — Datenminimierung by design: der LLM-Kontext bekommt SCHEMA + AGGREGATE,
nie Massendaten.** Das xlsx-/OCR-Prinzip (Modell orchestriert, Server rechnet
deterministisch) auf externe Quellen ausgedehnt. Der Steckbrief (Phase 7) ist die
Voraussetzung: ein Modell, das Schema + Rezepte kennt, kann BLIND orchestrieren —
es formuliert SQL/Code über Feldnamen, ohne je eine Datenzeile zu sehen. Damit
entfällt für Massendaten das Anonymisierungs-/Deanonymisierungs-Problem strukturell
(nichts im Kontext = nichts zu schützen), statt es per lokalem Modell oder
PII-Lauf über Millionen Zeilen zu erschlagen.

1. **`context_preview` pro Quelle** (`none|head|full`, Default `head` = heutige
   50 Zeilen): bei `none` liefert db_query/rest_query in den Kontext NUR
   Spaltenliste + row_count + (bei out=) Artefakt-Pfad — keine einzige Rohzeile.
   GDPR-Pass über den Preview bleibt für `head/full`; bei `none` gibt es nichts
   zu anonymisieren. Zusätzlich Tool-Parameter `preview` (darf den Quellen-Default
   nur RESTRIKTIVER machen, nie lockerer).
2. **Parquet-Export**: `out='name.parquet'` zusätzlich zu CSV (pyarrow ist im
   Server-Python, Quant-Workbench Phase 0) — der SQL-/REST-Extrakt wird EINMAL
   gezogen und landet als Artefakt; alle Folgeanalysen laufen lokal.
3. **Deterministische Kette dokumentiert + getestet**: `db_query(out=x.parquet)`
   → `data_query`/DuckDB-Aggregate/Joins/Pivots über das Artefakt →
   `xlsx_create`/Charts via `kernel_exec` — Rohdaten fließen ausschließlich
   Server-seitig (Session-Artefakt-Ordner), das Modell sieht Schema, Zeilenzahlen
   und die (kleinen) Aggregat-Ergebnisse. rest_query-Analogon: `out='x.json'` →
   data_query kann JSON (v9.318-Grid-Pipeline).
4. **Tool-Prosa als Steering**: db_query-/rest_query-/data_query-Beschreibungen
   nennen die Kette explizit („large results: export once, aggregate locally via
   data_query — do NOT page raw rows through the conversation"); der Steckbrief
   kann sie pro Quelle konkretisieren. Kein neues Gating — Steering + Defaults
   reichen für v1 (das Modell KANN bei `head` weiter kleine Ergebnisse direkt
   lesen; `none` erzwingt die Kette für sensible Quellen hart).
5. Tests: `context_preview:none` → Tool-Ergebnis enthält nachweislich keine
   Datenzeile (Characterization über das Ergebnis-JSON); Parquet-Roundtrip
   db_query→data_query zeilenidentisch zu CSV; preview-Parameter kann nicht
   lockern. **Erfolgskriterium (live):** Analyse-Frage über eine strict-Quelle
   (braintest mit `context_preview:none`) läuft Ende-zu-Ende — Query → Parquet →
   DuckDB-Aggregat → Chart — mit einem CLOUD-Modell; der Trace beweist: keine
   Rohzeile im Kontext, nur Schema/Zeilenzahlen/Aggregate. Kein GDPR-Block, kein
   Zwang zum lokalen Modell, kein Anonymisierungslauf über Massendaten.

### Phase 9 — Docs + Release
- Skill: 01-api (neue Endpoints + Felder inkl. generate_guide), 02-tools (Modus +
  Scope im db_query-Block, rest_query-Block, Steckbrief-Injektion), 04-recipes
  (Datenanbindung: MSSQL-Rezept inkl. db_datareader, rw-Warnung,
  Projekt-/Chat-Scoping-Anleitung, REST-Quelle, Steckbrief-Pflege,
  OnBase-Rezept aus Anhang A), 06-user-manual (DE: Admin-Tab-Update,
  Projekt-Sektion, Right-Panel-Tab), 05-internals (Scope- + Preamble-Mechanik),
  SKILL.md-Bump.
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
  Die Tests MÜSSEN denselben Stack fahren wie das Banknetz (pyodbc + msodbcsql17),
  nicht einen Ersatztreiber — sonst validiert Phase 1 den falschen Pfad.
- **O5 — REST-Pagination/Discovery:** rest_query paginiert NICHT automatisch
  (das Modell folgt selbst next-Links innerhalb der erlaubten Pfade); kein
  OpenAPI-Auto-Discovery in v1 — `allowed_paths` + Quellen-Steckbrief tragen
  die Semantik. Erst bei Bedarf erweitern.
- **O6 — MCP-Server-Referenz pro Quelle:** in v1 NICHT gebaut, aber als Stufe 3
  der Eskalationsleiter (E12) mit Kriterien dokumentiert. Wenn Stufe 3 kommt:
  `mcp_server`-Feld auf der Quelle, Tools via bestehendem MCPManager; der
  Quellen-Skill (Stufe 2) ist die Spezifikation des zu schreibenden Servers.
  Für OnBase: Hyland-Roadmap („Agent Builder"/Agent Mesh) beobachten, bevor
  selbst gebaut wird.
- **O7 — OpenAPI-Bootstrap für REST-Steckbriefe:** optionaler zweiter Schritt
  von generate_guide (URL fetchen, verdichten); v1 kann mit handgepflegtem
  REST-Steckbrief starten.

---

## Anhang A — Anwendungsfall Hyland OnBase (Recherche 2026-07-17)

OnBase (Hyland ECM/DMS) läuft auf **SQL Server oder Oracle** — die MSSQL-Anbindung
aus Phase 1 ist damit der direkte Weg. Zwei Anbindungswege, beide von diesem Plan
abgedeckt:

**Weg 1 — Read-only-SQL auf die OnBase-DB (empfohlen für Abfragen/Analysen):**
- Hyland dokumentiert das Reporting-Schema offiziell im **Database Reporting Guide**
  (docs.hyland.com): `ITEMDATA` = eine Zeile pro Dokument (PK `itemnum`),
  `DOCTYPE` = Dokumenttypen (Join auf ITEMDATA), `KEYITEM###`/`KEYTABLE###` =
  Keyword-Werte (Cross-Reference pro Keyword-Typ), `DISKGROUP`/`ITEMDATAPAGE` =
  physische Ablage, dazu Workflow-Tabellen. Metadaten-Analysen (Volumen je
  Dokumenttyp, Keyword-Auswertungen, Workflow-Durchlaufzeiten) sind reine
  SELECT-Joins über diese Tabellen.
- **Hylands Database Use Policy** (Database Reference Guide, Appendix A): direkte
  Queries sind „against Hyland's recommendation", aber SELECT ist als einziges
  DML explizit zulässig; Bedingungen: DBA-Review der Query-Pläne, Performance-
  Rücksicht auf die Produktions-DB. Konsequenzen für uns:
  1. OnBase-Quelle IMMER als `access_mode: ro` anlegen (unser Statement-Gate
     erzwingt Hylands SELECT-only-Policy technisch);
  2. bevorzugt gegen eine **Reporting-Replica** (Read-only-AG-Replica/Snapshot)
     statt der Produktions-DB — im Rezept dokumentieren;
  3. DB-Login nur `db_datareader` (Schicht 3, das MSSQL-Rezept aus Phase 1);
  4. **Tabellen-Whitelist aus Phase 3 passt exakt**: OnBase-Quelle im Projekt auf
     `ITEMDATA, DOCTYPE, KEYITEM…`-Sicht beschränken, der Rest der ~1000
     OnBase-Tabellen bleibt unsichtbar für den Agenten.
- Damit ist OnBase NUR Konfiguration (Quelle + Whitelist), kein Code über
  Phase 1–5 hinaus.

**Weg 2 — OnBase Document REST API (Hyland API Server):** neuere Foundation-Versionen
bieten eine REST-API (Dokument-Retrieval, Keyword-Typen/-Werte, Upload) über den
Hyland API Server (IIS, OAuth/IIS-Auth je Umgebung). Für Dokument-INHALTE und aktive
Integrationen der richtige Weg — als `type: rest`-Quelle (Phase 6) mit `base_url` des
API Servers, Bearer/Header-Auth und `allowed_paths` auf die Query-Endpoints. Für reine
Metadaten-Analysen ist Weg 1 mächtiger (SQL-Aggregate); Weg 2 ergänzt, wenn Inhalte
oder Hyland-supportete Zugriffe gefordert sind.

Quellen: Hyland Database Reporting Guide (ITEMDATA/Database Tables, docs.hyland.com),
Database Reference Guide Appendix A „Database Use Policy → Accessing the Database to
Retrieve Data", OnBase Document REST API (Content-Composer-Doku, docs.hyland.com).

---

## Anhang B — Bank-verifiziertes MSSQL-Verbindungs-Specimen (2026-07-17)

Destillat aus `sync_service.py` eines bestehenden internen Bank-Tools — **im Netz der
Bank produktiv, und dort der einzige funktionierende Weg** (die Herleitung war
aufwendig; nicht „modernisieren"). Maßgeblich für den `mssql`-Branch in Phase 1.

**Verbindungsaufbau (SQL-Auth und Windows-Auth):**

```python
import pyodbc
DRIVER = os.environ.get('MSSQL_DRIVER', 'ODBC Driver 17 for SQL Server')

# SQL Server Authentication
conn_str = (
    f"DRIVER={{{DRIVER}}};"
    f"SERVER={host},{port};"        # KOMMA zwischen Host und Port!
    f"DATABASE={database};"
    f"UID={username};"
    f"PWD={password}"
)
# Windows Authentication (Banknetz-Alternative)
conn_str = (
    f"DRIVER={{{DRIVER}}};"
    f"SERVER={host},{port};"
    f"DATABASE={database};"
    f"Trusted_Connection=yes;"
)

conn = pyodbc.connect(conn_str, timeout=30)   # timeout = LOGIN-Timeout (Sekunden)
conn.timeout = 60                             # QUERY-Timeout, separat setzen
# Test-Connection im Specimen: pyodbc.connect(conn_str, timeout=5)
```

**Was das Specimen bewusst NICHT setzt** (und wir auch nicht): `Encrypt=`,
`TrustServerCertificate=`, `MARS_Connection=`, TDS-Versionen. Driver 17 verbindet
mit `Encrypt=no`-Default — genau das trägt im Banknetz. Driver 18 würde mit seinem
`Encrypt=yes`-Default an Self-Signed-Zertifikaten der on-prem-Server scheitern.

**Metadaten-Queries (bank-erprobt, für tables-Endpoint + generate_guide):**

```sql
-- Datenbanken (ohne Systemdatenbanken):
SELECT name FROM sys.databases WHERE database_id > 4 ORDER BY name
-- Tabellen:
SELECT TABLE_NAME FROM [db].INFORMATION_SCHEMA.TABLES
 WHERE TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME
-- Spalten (parametrisiert, ?-Platzhalter):
SELECT COLUMN_NAME FROM [db].INFORMATION_SCHEMA.COLUMNS
 WHERE TABLE_NAME = ? ORDER BY ORDINAL_POSITION
```

**Fehlerdiagnose-Mapping** (pyodbc-SQLSTATE, fürs saubere Fehlertext-Handling):
`08001` = Verbindung (Host/Port/Firewall/TCP-IP-Protokoll), `28000` = Login
fehlgeschlagen (auch: SQL-Auth-Modus serverseitig deaktiviert), „driver"-Fehler =
ODBC-Treiber fehlt/falscher Name. Identifier-Quoting durchgängig `[eckige Klammern]`.
