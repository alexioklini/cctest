# Quant-Workbench — Umsetzungsplan (Phasen 0 → D → B → C → A)

**Stand:** 2026-07-16, Basis v9.353.2. Erarbeitet in der Session „Claude Science → brain-agent",
geschärft durch claude.com/solutions/financial-services und vier User-Entscheidungen (unten).
Artefakt-Studie: https://claude.ai/code/artifact/4576b14c-dcd8-419b-896a-0cdd3c35acd8

**Ziel:** Finanzmathematik, Simulation und Compliance-Auswertung in brain-agent — Python, SQL
und R in einem reproduzierbaren Notebook-Workflow. Positionierung: nicht „Jupyter mit KI",
sondern **Notebooks, die durch die Modellvalidierung kommen** (BCBS 239 / MaRisk AT 4.3.2 —
jede Zahl führt auf Code + Datenstand + Environment zurück). Der Burggraben ist der bestehende
Compliance-Stack (PII/GDPR-Scanner, ARL-Klassifizierung, lokale Modelle, Kosten-Audit) — ein
nacktes Anaconda hat nichts davon.

**User-Entscheidungen (2026-07-16, verbindlich):**
1. Datenquellen: Warehouse/echte DB **und** XLSX/CSV-Uploads **und** Parquet-Extrakte (alle drei).
2. Zielgruppe: Quants **und** Compliance/Validierung **und** Fachbereich — das Notebook muss
   zugleich Werkzeug, Prüfartefakt und Bericht sein.
3. R: **echter Bedarf, bestehender R-Code** muss laufen (rechtfertigt IRkernel voll).
4. Startpunkt: **Environment** (Phase 0).

---

## Verifizierte Ist-Fakten (nicht erneut prüfen, außer bei Zweifel)

| Fakt | Beleg |
|---|---|
| `python_exec` = frischer Subprozess pro Call, KEIN Kernel | `engine/tools/file_tools.py:4507` (`subprocess.Popen([sys.executable, script_path])`) |
| Timeout 30 s Default, `max_output_chars` 50000 | `file_tools.py:4425-4426`, `tools_config.json → python_exec` |
| `venv_path` wird als **PYTHONPATH** injiziert, NICHT als Interpreter | `file_tools.py:4499-4500` |
| Server-Interpreter = **Homebrew python3** (aktuell 3.14) | launchd plist `ProgramArguments`, `~/Library/LaunchAgents/com.brain-agent.server.plist` |
| Installiert: numpy 2.4, pandas 3.0, scipy, sklearn, **duckdb 1.5.2, pyarrow 24.0**, openpyxl | live geprüft |
| FEHLT: matplotlib, seaborn, statsmodels, arch, QuantLib, **Rscript** | live geprüft |
| SELECT-only-Modell existiert (3 Schichten) | `engine/tools/xlsx_tools.py:747-757` (Prefix-Check + sqlite-Authorizer + `PRAGMA query_only=ON`) |
| Keine DB-Konnektoren im Repo (kein psycopg/sqlalchemy/snowflake) | grep verifiziert |
| Keinerlei `.ipynb`-Behandlung (weder ingest noch Renderer) | grep verifiziert |
| Artefakt-iframe rendert mit `sandbox="allow-scripts allow-same-origin"` | `web/js/panels_artifacts.js:839` |
| Renderer-Dispatch = `switch(type)` auf servergesetztem Typ | `panels_artifacts.js:836` (`renderArtifactContent`) |
| Typ-Zuordnung | `brain.py:17136` (`_ARTIFACT_TYPE_MAP`), Rolle: `brain.py:17152` (`_ARTIFACT_INTERMEDIATE_EXTS`) |
| `artifact_versions`-Schema: id/artifact_id/version/content/size/message_idx/action/created_at/thumbnail | `server_lib/db.py:404-422` |
| Skript-Registrierung + Ordner-Diff kennen Skript UND erzeugte Datei gleichzeitig | `file_tools.py:4494` (`_after_file_write(script_path,…)`), `:4068` (`_register_new_artifacts`) |
| Wire-only-Seam (Vorlage aus Design-Modus) | `handlers/chat.py:388` (`_inject_web_preamble_into_wire`), Aufrufmuster `:4936-4942`, Gate `:7924` |
| Prozess-Supervisor-Vorlage | `server_lib/sidecar_supervisor.py:322` (`Crawl4aiSupervisor(ProcessSupervisor)`) |
| Serialisierungs-Vorlage | `brain.py:8458` (`LocalProviderQueue`) |
| Langlebiger-Prozess-Präzedenzfall | MLX-OCR = EIN langlebiger Thread (v9.328; Thread-Exit crasht Metal) |
| Existiert bereits (NICHT neu bauen): Literatursuche, Skills-aus-Chat | `science_search` (web-Gruppe), Skill-Gen v9.294 |

## Globale Regeln für jede Phase

- **Neues Tool = 4 Sites / 3 Dateien**: Schema in `TOOL_DEFINITIONS` (`engine/tool_schemas.py`),
  `TOOL_GROUPS` + `TOOL_DISPATCH` (`brain.py`, Dispatch = **direkte Fn-Ref**, kein Lambda),
  Impl in `engine/tools/<gruppe>.py` (brain via lazy `import brain as _brain`).
- **KV-Prefix-Stabilität**: NIE per-User/per-Session-Inhalte in `_build_system_prompt`.
  Turn-Kontext nur wire-only über `_inject_web_preamble_into_wire`. Schema-/Tool-Änderungen
  invalidieren den Warm-Pool-Prefix einmalig (legitim, re-primt).
- **Massendaten fließen NIE durchs Modell** (xlsx_query-Prinzip): Modell liefert Intent
  (SQL/Spec), Server bewegt Daten. Anzeige gekappt (50 Zeilen), volles Ergebnis als Artefakt.
- **GDPR**: jedes Tool-Ergebnis mit potenziell sensiblen Daten durch `_gdpr_anon_tool_text`.
- **Ein-Schreiber-Invariante**: nur der Agent schreibt Artefakte. KEIN Zellen-Editor in der UI.
- **JS**: `cd web/js && ./js_gate.sh` (Baseline aktuell **2006**; +N nur bewusst im selben
  Commit, oder null neue Globals via bestehendem IIFE). Server muss laufen für den Smoke.
- **Python**: `py_compile` auf jede geänderte Datei; Neustart via `launchctl` (NIE SIGKILL);
  danach `/v1/status` prüfen; Logs in `server.error.log`.
- **Doku im SELBEN Commit**: brain-agent-guide-Skill (02-tools/03-storage/06-user-manual je
  nach Fläche) + `engine/changelog_curated.py` bei user-sichtbaren Features + VERSION an
  beiden Stellen. Pre-push-Hook warnt.
- **Test-Sessions sofort löschen** (feedback_cleanup_test_sessions).
- **Phasen-Gate**: Kern-Mechanik validieren VOR UI/Config-Ausbau (feedback_phase_a_then_validate).
  Jede Phase endet mit erfüllten Erfolgskriterien — nicht mit „Code geschrieben".

---

## Phase 0 — Environment: Quant-Python + R-Laufzeit  (~1–2 h)

**Ziel:** `python_exec` kann Figuren und Quant-Statistik erzeugen; R-Skripte sind ausführbar.

### Schritte
1. **Quant-venv bauen — mit dem SERVER-Interpreter** (ABI-Falle: `venv_path` wird als
   PYTHONPATH injiziert, der Interpreter bleibt `sys.executable` = Homebrew-Python):
   ```bash
   /opt/homebrew/bin/python3 -m venv .venv_quant
   .venv_quant/bin/pip install matplotlib seaborn statsmodels arch QuantLib
   ```
   (`.venv_quant` in `.gitignore`; duckdb/pyarrow sind schon global da, nicht doppeln.)
   Falls QuantLib-Wheel für py3.14 fehlt: weglassen, im Plan-Log vermerken — kein Blocker,
   scipy+statsmodels+arch decken VaR/GARCH/Regression ab.
2. **`tools_config.json → python_exec`**: `venv_path` auf `<repo>/.venv_quant/lib/python3.14/site-packages`
   setzen; `timeout` 30 → **120** (Analysen; Phase A entlastet das später).
3. **R-Laufzeit**: `brew install r`; Smoke `Rscript -e 'cat(1+1)'`. IRkernel erst in Phase A.
4. **`r_exec`-Tool** (bestehender R-Code, User-Entscheidung 3 — kein YAGNI):
   Spiegel von `tool_python_exec` in `engine/tools/file_tools.py` daneben, `Rscript script_N.R`
   statt `sys.executable`; gleicher Ordner-Diff (`_snapshot_dir`/`_register_new_artifacts`),
   gleiche cwd-Logik, gleicher GDPR-Pass auf stdout, `register_tool_process` für Kill.
   4-Site-Regel; Gruppe `code_exec`. Beschreibung mit Verweis „für Python nutze python_exec".

### Erfolgskriterien (messbar)
- Chat-Turn „plotte eine Normalverteilung als PNG" → PNG-Artefakt erscheint im Panel.
- `python_exec` mit `import statsmodels, arch` → kein ImportError.
- `r_exec` mit kleinem Chain-Ladder-ähnlichen Beispiel (data.frame + Aggregat) → stdout korrekt,
  geschriebenes CSV erscheint als Artefakt.
- 90-s-Skript läuft durch (Timeout-Erhöhung wirksam).

### Doku
Skill 02-tools (r_exec), curated-Changelog-Eintrag (user-sichtbar: „Diagramme + R"), VERSION.

---

## Phase D — Datenlayer: SQL über alle Quellen, read-only erzwungen

**Ziel:** Ein Analyst fragt Warehouse, Parquet-Extrakte und Uploads mit reinem SELECT ab;
Massendaten bleiben serverseitig. Zwei Teilphasen — D1 zuerst liefern und validieren.

### D1 — Parquet/CSV via DuckDB (billig, Engine schon installiert)
1. Neues Tool **`data_query(paths, sql, out?)`** in neuem `engine/tools/data_tools.py`
   (4-Site-Regel; Gruppe `documents`, neben den xlsx_*-Tools).
   - `paths`: .parquet/.csv/.duckdb-Dateien (Artefakt-Ordner oder Projekt-Input); je Datei
     eine View mit sanitisiertem Namen (Namensschema von `xlsx_inspect` übernehmen — der
     „nie Bezeichner raten"-Hebel).
   - **Read-only, dreischichtig sinngemäß**: DuckDB hat keinen sqlite-Authorizer, also
     (a) Prefix-Check SELECT/WITH + Multi-Statement-Reject (aus `xlsx_tools` herausfaktorieren,
     NICHT kopieren — Single-Fix-Point), (b) `duckdb.connect(':memory:')` + Views statt
     Schreibzugriff aufs Original, (c) `SET enable_external_access=false` nach dem
     View-Aufbau (verhindert COPY TO / weitere Dateizugriffe aus dem SQL heraus).
   - SQL-Fehler echoen das Schema (Selbstkorrektur in einer Runde, wie xlsx_query).
   - Anzeige 50 Zeilen + row_count; `out='name.csv'` schreibt volles Ergebnis via
     `_enforce_artifact_path` + `_after_file_write`. Ergebnis durch `_gdpr_anon_tool_text`.
   - Guardrails: 30 MB / 200k Zeilen wie xlsx_query.
2. `xlsx_query` bleibt UNVERÄNDERT (sqlite-Pfad für xlsx bewährt). `xlsx_inspect`-Beschreibung
   um einen Verweis ergänzen („.parquet/.csv → data_query").

**Erfolgskriterien D1:** 1-Mio-Zeilen-Parquet: `GROUP BY`-Aggregat < 5 s, im Chat nur das
Aggregat; `INSERT`/`COPY TO`/Multi-Statement werden abgelehnt (Testfälle wie
`tests/test_xlsx_tools.py`, die drei Rejections explizit).

### D2 — Warehouse-Konnektor (erst nach validiertem D1)
1. `config.json → data_sources`: `[{name, type: postgres|mssql|snowflake|oracle, dsn|env_key,
   options}]` (gitignored, per-Maschine — wie `crawl4ai`-Block). Treiber **lazy** importieren
   (psycopg2/snowflake-connector nur bei Nutzung; fehlend → klare Fehlermeldung, kein Crash).
2. Neues Tool **`db_query(source, sql, out?)`** in `data_tools.py`:
   - Schicht 1: derselbe herausfaktorierte Prefix-Check.
   - Schicht 2: session-read-only wo die API es hergibt (postgres:
     `default_transaction_read_only=on`; snowflake: read-only-Role im DSN).
   - Schicht 3 (organisatorisch, im Tool-Ergebnis dokumentiert): der DB-User in `data_sources`
     MUSS ein Read-only-Grant sein — der Plan schreibt das als Betriebsvoraussetzung fest.
   - Timeouts (Statement-Timeout serverseitig setzen), Row-Cap, GDPR-Pass, out=CSV-Artefakt.
   - `db_list_sources()` NICHT bauen — die Tool-Beschreibung nennt die konfigurierten Namen
     nicht (per-Maschine); das Modell bekommt sie via Fehlermeldung („verfügbar: …“) beim
     ersten Fehlgriff. Einfachheit vor Discovery-Tool.
3. Admin-UI: NICHT in D2 (config.json reicht; UI erst bei echtem Multi-User-Bedarf — Regel 2).

**Erfolgskriterien D2:** Gegen eine lokale Test-Postgres: SELECT liefert, INSERT wird von
Schicht 1 UND (nachweisbar, Log) von Schicht 2 abgelehnt; abgerissene Verbindung → sauberer
Tool-Fehler, kein Turn-Abbruch.

### Doku
Skill 02-tools (+ 04-recipes „Datenanbindung“), curated-Eintrag (admin-sichtbar), VERSION.

---

## Phase B — Compliance-Provenance  (additive Migration, hoher regulatorischer Wert)

**Ziel:** Jede erzeugte Datei beantwortet: welcher Code, welcher Datenstand, welches Environment,
wann. Aus `message_idx` (existiert) wird eine vollständige Nachweiskette.

### Schritte
1. **Migration** (additiv, Muster `thumbnail` `db.py:417-422`): `artifact_versions` +=
   `produced_by TEXT` (Pfad des erzeugenden Skripts, relativ zum Session-Ordner, z. B.
   `script_3.py`; später `notebook.ipynb#cell-3`) und `env_snapshot TEXT`
   (Kurzform `py3.14|numpy 2.4.2|pandas 3.0.2|…`, einmal pro Prozessstart berechnet + gecacht).
2. **Durchreichen am Choke-Point**: `_register_new_artifacts` (`file_tools.py:4068`) kennt
   `script_path` bereits → optionaler kwarg `produced_by=None` durch
   `brain._after_file_write` (`brain.py:17273`) → `_register_artifact_version`
   (`brain.py:17215`) → `add_artifact_version` (`db.py:1246`). Default `None` hält alle
   anderen Aufrufer (write_file, xlsx_create, …) unverändert — additiv.
   `python_exec` + `r_exec` setzen es; `execute_command` NICHT (kein zuordenbares Skript —
   ehrlich leer lassen statt raten).
3. **UI**: Versions-Detail im Artefakt-Panel zeigt die Chips (Code / Env / Turn / Zeit);
   `produced_by` klickbar → öffnet das Skript-Artefakt. Null neue Globals anstreben
   (bestehende Render-Funktion erweitern).
4. **SSE**: `artifact_updated`-Payload (`brain.py:17319`) um `produced_by` ergänzen (Client
   ignoriert unbekannte Felder — abwärtskompatibel).

### Erfolgskriterien
- `python_exec` schreibt PNG → Versions-Detail zeigt `script_N.py` + Env-String; Klick
  öffnet das Skript. Ältere Versionen (pre-Migration) zeigen „—" statt zu brechen.
- `write_file`-Artefakte: `produced_by` leer, kein Regressions-Fehler (Characterization-Test).

### Doku
Skill 03-storage (Schema) + 06-user-manual (Nachweiskette, DE), curated-Eintrag
(user-sichtbar: „Jede Zahl führt auf ihren Code zurück"), VERSION.

---

## Phase C — Notebook-Renderer  (.ipynb als erstklassiges Artefakt)

**Ziel:** Der Agent liefert `.ipynb` als Ergebnisdokument; das Panel rendert Zellen, Tabellen,
Bilder, Markdown. Jede Version = ein prüfbarer Stand. (Ausführung erst Phase A — in C schreibt
der Agent das JSON selbst oder via python_exec/nbformat.)

### Schritte
1. `brain.py:17136` `_ARTIFACT_TYPE_MAP`: `"ipynb": "notebook"`. NICHT in
   `_ARTIFACT_INTERMEDIATE_EXTS` (Notebook ist Output/Prüfartefakt, Rolle `output`).
2. `panels_artifacts.js:836` `renderArtifactContent`: `case 'notebook':` — JSON parsen
   (defensiv: Parse-Fehler → Fallback auf Code-Ansicht), Zellen iterieren:
   - `markdown`-Zelle → `renderMarkdown()`
   - `code`-Zelle → hljs, Sprache aus `metadata.kernelspec.language` (python/R/sql)
   - Outputs: `text/plain` als `<pre>`; `image/png` als `<img data:>`;
     **`text/html`-Outputs in ein EIGENES `sandbox`-iframe** (nie direkt ins Panel-DOM —
     fremdes HTML, XSS-Fläche); `application/json` über den bestehenden Tree-Renderer.
   - Null neue Globals anstreben (private Helper in der bestehenden Datei-Struktur);
     sonst Baseline 2006 im selben Commit bumpen.
3. **Ingest**: `_extract_ipynb` in `engine/doc_convert.py` (stdlib-json, KEIN nbformat-Dep:
   markdown-Zellen verbatim, code-Zellen als ```-Fences, Outputs nur text/plain) →
   `_EXTRACTORS` (`doc_convert.py:1991`) + `SUPPORTED_EXTS`; NICHT in `_markitdown_exts`.
   Damit sind Notebooks in Projekt-Mining + PII-Scan + Klassifizierung drin (ein Dispatcher,
   vier Konsumenten).
4. **Tool-Steering**: `write_file`-Beschreibung um einen Satz ergänzen (Analyse-Ergebnisse
   gern als `.ipynb` mit eingebetteten Outputs) — KEIN neues Tool in dieser Phase.

### Erfolgskriterien
- Agent erzeugt per python_exec ein Notebook mit md+code+PNG-Output → Panel rendert alle
  drei Zelltypen; PNG sichtbar; HTML-Output bleibt im Sandbox-iframe (im DOM verifizieren).
- Version 1 vs. Version 2 im Verlauf einzeln aufrufbar.
- Projekt-Mining eines `.ipynb` erzeugt Companion-`.md` mit Zellinhalten.
- js_gate GRÜN (inkl. Smoke bei laufendem Server).

### Doku
Skill 02-tools/06-user-manual, curated-Eintrag (user-sichtbar), VERSION.

---

## Phase A — Persistente Kernel (Python + R)  (größter Hebel, größtes Risiko — zuletzt)

**Ziel:** Positionen einmal laden, dann iterieren. Python-DataFrame und R-Objekt im selben
Sitzungslauf. Kernel sterben mit Brain (KEIN Überleben über Restart — bewusst, wie der
in-process Loop; Restart-Recovery wäre Scope-Creep).

### Architektur-Entscheidungen (vorab festgezurrt)
- **jupyter_client + ipykernel** (Python) / **IRkernel** (R), installiert in `.venv_quant`
  bzw. der R-Library. Kernel = eigener OS-Prozess → kein Metal/MLX-Konflikt mit dem
  Server-Prozess (Lehre aus v9.328 beachtet, aber hier unkritisch, da Subprozess).
- **KernelManager** als neues `engine/kernels.py` + Verdrahtung in `server_daemons.py`:
  max. N Kernel gleichzeitig (Start: 3), **ein Kernel pro Session** (Key = session_id),
  Idle-Timeout 20 min (Reaper-Thread), LRU-Verdrängung bei Vollbelegung, RAM nicht selbst
  messen (V1: Prozess-RSS nur anzeigen, nicht enforcen — Einfachheit).
- **Ein Exec gleichzeitig pro Kernel** (Lock pro Kernel; Muster `LocalProviderQueue`
  `brain.py:8458`, aber pro-Kernel statt pro-Provider). Kein Queuing über Kernel hinweg nötig.
- **Kill-Pfad dreifach**: (a) Tool-Cancel via `register_tool_process`-Analogon
  (Kernel-Interrupt, dann SIGKILL nach Frist), (b) UI-Button „Kernel neu starten",
  (c) Brain-Shutdown killt alle (atexit + Supervisor-Stop; Vorlage
  `sidecar_supervisor.py:322`).
- Kernel-cwd = Session-Artefakt-Ordner (dieselbe Choke-Point-Logik wie `python_exec` —
  `open('x.png','w')` ist by construction richtig; Ordner-Diff registriert Outputs).

### Schritte
1. `pip install jupyter_client ipykernel` in `.venv_quant`; `Rscript -e 'IRkernel::installspec()'`.
2. `engine/kernels.py`: KernelManager (start/execute/interrupt/shutdown/reap); Outputs des
   Kernels (`display_data`/`execute_result`): `image/png` → Datei in den Session-Ordner
   schreiben → `_after_file_write(..., produced_by=f"kernel#{exec_count}")` (Phase-B-Kette
   greift automatisch); `text/plain` gekappt zurück an den Loop; stderr/Traceback verbatim.
3. **Tools (4-Site-Regel, Gruppe `code_exec`)**:
   - `kernel_exec(code, lang='python'|'r')` — startet den Session-Kernel lazy beim ersten Call.
   - `kernel_status()` — Sprache, Uptime, RSS, definierte Top-Level-Namen (Python: kleines
     Introspektions-Snippet; R: `ls()`), letzter Exec-Zähler.
   - `kernel_restart()` — expliziter Neustart.
   - `python_exec`/`r_exec` BLEIBEN (Einmal-Skripte, Scheduler-Läufe); Beschreibungen
     gegeneinander abgrenzen („Iteration auf großen Daten → kernel_exec").
4. **GDPR**: kernel_exec-Ausgaben durch `_gdpr_anon_tool_text` (wie python_exec stdout).
5. **UI (klein)**: Statusleisten-Badge „Kernel lebendig · py/R · RSS" + Restart-Button;
   SSE-Event `kernel_status` (bestehendes Event-Vokabular erweitern). Kein Zellen-Editor.
6. **Scheduler/Background**: kernel_exec dort NICHT anbieten (purpose-Gating in
   `tool_settings`) — Scheduled Tasks bleiben auf python_exec (stateless by design;
   ein Kernel pro sched-Session wäre Leak-Fläche ohne Nutzen).

### Erfolgskriterien
- Turn 1: 1-Mio-Zeilen-Parquet in DataFrame laden (~Sekunden). Turn 2: Aggregat auf dem
  DataFrame **ohne Neuladen** — nachweisbar an der Laufzeit (<1 s) und `kernel_status`.
- R: Objekt aus Turn 1 in Turn 2 verfügbar; PNG aus `plot()` erscheint als Artefakt MIT
  `produced_by`-Chip.
- Kill-Matrix: Endlosschleife per Cancel abbrechbar (Kernel überlebt, Interrupt); zweiter
  Cancel → Kernel-Kill + sauberer Tool-Fehler; Brain-Restart → keine Zombie-Prozesse
  (`pgrep -f ipykernel` leer).
- Idle-Reaper: Kernel nach 20 min Leerlauf weg, nächster kernel_exec startet transparent neu.
- 3 parallele Sessions à 1 Kernel: keine Cross-Session-Leaks (Variablen isoliert).

### Doku
Skill 02-tools/05-internals/06-user-manual, curated-Eintrag (user-sichtbar: „Daten bleiben
zwischen Fragen geladen"), VERSION. Handover-Notiz in dieses Dokument (Abschnitt „Log").

---

## Bewusst NICHT (Scope-Guardrails — bei Bedarf User fragen, nicht still bauen)

- **Schreibender DB-Zugriff** — read-only ist die Invariante des gesamten Datenlayers.
- **Marktdaten-Terminals** (LSEG/FactSet/Bloomberg) — Lizenz-/Vertragsthema, kein Code.
- **HPC/GPU-Cluster, Modal, SSH-Submission** — kein belegter Bedarf.
- **Notebook-Zellen-Editor in der UI** — Zwei-Schreiber-Konflikt, verletzt Ein-Schreiber-Invariante.
- **Kernel-Überleben über Brain-Restart** — bewusst „stirbt mit Brain" (wie in-process Loop).
- **Admin-UI für data_sources** in D2 — config.json reicht zunächst.
- **nbformat/nbconvert als Dependency** — stdlib-json genügt für Rendern + Ingest.

## Log (von den Umsetzungs-Sessions zu pflegen)

- 2026-07-16: Plan erstellt (Basis v9.353.2). Noch keine Phase begonnen.
- 2026-07-16: **Phase 0 ABGESCHLOSSEN** (v9.354.0). `.venv_quant` (Homebrew py3.14)
  mit matplotlib/seaborn/statsmodels/arch/**QuantLib 1.43** (py3.14-Wheel existiert —
  der Vorbehalt aus Schritt 1 griff nicht); `python_exec.venv_path` + timeout 120;
  R 4.6.1 via brew; `r_exec` gebaut (4 Sites, Spiegel von python_exec). Alle 4
  Erfolgskriterien live verifiziert (glm-5.2). ABWEICHUNGEN/FUNDE: (a) `python_exec`
  stand global auf `states.interactive=inactive` (Alt-Seed der Tool-Matrix) — Modelle
  wichen auf execute_command aus; auf `active` gesetzt (config.json `tool_settings`).
  (b) `r` zu `_ARTIFACT_INTERMEDIATE_EXTS` ergänzt (Skript-Rollen-Parität py/R).
  (c) r_exec bewusst NICHT in `GDPR_ARGS_DEANON_TOOLS` (deny-by-default; der
  Local-Safe-Check parst kein R) und NICHT in `_WORKFLOW_STEP_TOOLS`. Nächste Phase: D1.
- 2026-07-16: **Phase D1 ABGESCHLOSSEN** (v9.355.0). `data_query` gebaut
  (`engine/tools/data_tools.py`, 4 Sites, Gruppe documents; `_check_select_only`/
  `_sanitize_name` aus xlsx_tools IMPORTIERT). Erfolgskriterien live verifiziert
  (glm-5.2): 1-Mio-Zeilen-Parquet-Aggregat, Tool-Latenz 0.025 s (<5 s), nur das
  Aggregat im Chat, Schema-Echo-Selbstkorrektur in einer Runde; die drei
  Rejections (INSERT/COPY TO/Multi-Statement) explizit getestet
  (`tests/test_data_tools.py`, 14 Tests). ABWEICHUNGEN/FUNDE: (a) Schicht (c)
  präzisiert — `SET enable_external_access=false` allein hätte die LAZY-Views
  gebrochen; gelöst über `allowed_paths` = exakt die Eingabedateien +
  `lock_configuration=true` (blockt auch Re-Enable). Reihenfolge-Invariante:
  `.duckdb` MUSS vor dem Lockdown READ_ONLY-attacht werden (WAL-Sidecars).
  (b) Datei-Kappe 512 MB statt „30 MB wie xlsx_query" — die 30 MB existieren
  wegen SQLite-Materialisierung; data_query streamt, 30 MB hätte D1s Zweck
  (große Extrakte) konterkariert. Ergebnis-Kappe 200k Zeilen wie geplant.
  (c) Kein data_inspect nötig: jedes Ergebnis listet die Views, Fehler echoen
  das Schema. (d) data_query in `GDPR_ARGS_DEANON_TOOLS` + `_WORKFLOW_STEP_TOOLS`
  (Parität xlsx_query, anders als r_exec). Nächste Phase: D2 — erst nach
  validiertem D1-Betrieb bzw. User-Go (braucht lokale Test-Postgres).
- 2026-07-16: **Phase D2 ABGESCHLOSSEN** (v9.356.0, User-Go im selben Chat).
  `db_query(source, sql, out?)` + `config.json → data_sources` (Boot-Copy in
  server.py — 9.294.3-Falle vermieden). Alle 3 Erfolgskriterien live erfüllt:
  SELECT liefert (glm-5.2 erkundet information_schema selbst), INSERT von
  Schicht 1 UND nachweisbar von Schicht 2 abgelehnt (Owner-Credentials +
  direkter INSERT → `ReadOnlySqlTransaction`, Test im Log), Postgres mitten in
  der Session gestoppt → sauberer Tool-Fehler, kein Turn-Abbruch.
  ABWEICHUNGEN/FUNDE: (a) NUR type=postgres verdrahtet — mssql/snowflake/oracle
  wären untestbare spekulative Branches; fail-loud »not wired yet«, Nachrüsten
  = ein isolierter Branch in `_connect_readonly` sobald ein echter DSN existiert.
  (b) SECURITY-Beifang: `scripts/scrub_config.py` redigierte `dsn` nicht —
  DSN-Passwörter wären via pre-commit in config.example.json geleckt; Marker +
  Platzhalter ergänzt. (c) Server-side (named) Cursor statt Default-Cursor —
  psycopg2 lädt sonst das ganze Resultset in den Brain-Prozess. (d) db_query
  NICHT in `_WORKFLOW_STEP_TOOLS` (externe DB, deny-by-default wie r_exec).
  Test-Infra per-Maschine: postgresql@17 (brew-Service), DB `braintest`
  (50k-Zeilen `positionen`, Role `brain_ro`), psycopg2-binary im
  Server-Interpreter. Damit ist Phase D KOMPLETT. Nächste Phase: B oder C.
- 2026-07-16: **Phase B ABGESCHLOSSEN** (v9.357.0). Migration + Kwarg-Kette +
  UI-Chips + SSE exakt nach Plan; Setzer python_exec/r_exec (inkl.
  output.txt-Fallback), execute_command/write_file ehrlich NULL (per Test
  gepinnt). env_snapshot liest venv-dist-info via
  `importlib.metadata.distributions(path=[venv])` (kein Subprozess nötig;
  venv gewinnt über Server-site-packages, da der exec-Subprozess mit
  PYTHONPATH=venv läuft). Erfolgskriterien live: PNG per python_exec →
  Chips `script_1.py` + Env-String im Panel, Klick öffnet das Skript
  (Playwright im echten Browser verifiziert); Versionen ohne Provenance
  (render_diagram, Pre-Migration) blenden die Leiste aus, kein Bruch.
  ABWEICHUNG: keine — Plan 1:1. Nächste Phase: C (Notebook-Renderer).
- 2026-07-16: **Phase C ABGESCHLOSSEN** (v9.358.0). Alle 4 Schritte Plan-1:1
  (Typ-Map, Renderer-Case mit sandbox-iframe für text/html, _extract_ipynb
  stdlib-json, write_file-Steering-Satz). Alle 4 Erfolgskriterien live erfüllt
  (glm-5.2 + Playwright-DOM-Check): md/code/PNG rendern, HTML-Output
  nachweislich NUR im `sandbox=\"\"`-iframe (kein <table> im Panel-DOM),
  v1/v2 einzeln aufrufbar, Companion-.md via convert_one, js_gate grün
  (net-globals 2006 unverändert). Ergänzt über den Plan hinaus: error-Outputs
  (ANSI-bereinigte Tracebacks, rot) + application/json-Fallback auf <pre>.
  Damit sind **Phasen 0, D1, D2, B, C komplett** — offen ist nur noch
  Phase A (persistente Kernel; größtes Risiko, braucht jupyter_client/
  IRkernel + KernelManager + Kill-Matrix; vor Start User-Go einholen).
