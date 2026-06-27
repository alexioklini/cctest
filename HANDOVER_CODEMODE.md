# Handover — Code-Mode Workspace (CodeGraph-Ersatz, Terminal, Editor)

**Stand:** 2026-06-27, brain VERSION **9.218.0**, live + alles committed & gepusht
(`main...origin/main`, working tree clean). Letzter Commit `e9753487`.

Dieses Dokument fasst die gesamte Arbeit dieser Session zusammen, damit eine neue
Session nahtlos weitermachen kann. Ergänzende Detail-Memories (im
`memory/`-Ordner, werden automatisch geladen):
- `project_codegraph_replacement_eval` — CodeGraph→codebase-memory Eval + Umbau
- `project_codemode_terminal` — Terminal + Editor (Frontend/Backend-Details)
- `project_weburl_miner_path_bug` — der nebenbei gefixte Miner-Bug
- `feedback_evals_line_buffered`, `feedback_eval_single_run_noise` — Eval-Disziplin

---

## 1. Was in dieser Session gebaut wurde (alles LIVE auf 9.218.0)

### A. CodeGraph → codebase-memory-mcp (9.214.0 + 9.215.x)
Der in-tree Tree-sitter-CodeGraph wurde **vollständig ersetzt** durch die
brain-verwaltete **codebase-memory-mcp**-Binary (C, MIT, v0.8.1).
- **Warum:** Eval (`eval/codegraph_e2e.py`, agentisch, 4 Modelle × 3 Reps) zeigte:
  alter CodeGraph kann Code **nicht ENTDECKEN** (0/3 über alle Modelle, Modell
  rudert 14-19 Tool-Calls und gibt auf). cbm + **gemma-4-12B** = **3/3/3** in 1-2
  Calls. Lokales Code-Modell der Wahl = **gemma-4-12B-it-qat-4bit** (NICHT Ornith-35B).
- **Integration MemPalace-Stil (NICHT MCP):** Binary unter `.codebase-memory/bin/`
  (gitignored, 269MB, per-Maschine), pro Tool-Call als CLI-Subprozess mit
  Hard-Kill-Timeout. Code: `engine/tools/codebase_memory.py`.
- **4 Tools** (ersetzen die alten `code_graph_*`): `code_search` (BM25/Regex/
  semantic_query), `code_trace` (Aufrufer/Aufgerufene), `code_query` (Cypher),
  `code_snippet` (Quelle lesen). Plus Introspektion: `index_status`,
  `per_file_state`, `graph_overview`, `index_repository`.
- **Per-Tenant:** globaler brain-Quell-Index (für Brainy) + ein Index pro
  Code-Mode-Projekt unter `<pdir>/.cbm-cache`, geroutet via
  `request_context.code_graph_db` (= Cache-DIR, umgewidmet).
- **Lifecycle:** Index beim BRAIN.md-Schreiben (`engine/code_init.py`) + frisch
  gehalten vom **code-index-sync-Daemon** (`server_daemons.py`, mtime-Poll,
  entprellt; Hooks `_code_index_request`/`_code_index_status`/`_code_index_runs`).
- **INVARIANTE:** BRAIN.md darf NICHTS duplizieren, was der (auto-aktuelle) Index
  liefert — nur dauerhaftes Wissen (Zweck/Konventionen/Build). `_INIT_PROMPT` +
  Code-Mode-System-Prompt (`engine/prompt_build.py`) erzwingen das.
- **UI (9.215.x):** Index-Status-Chip (endnutzer-verständlich, kein Knoten/Kanten-
  Jargon) + Icon-Buttons (Aktualisieren/Bereinigen/Graph/Verlauf) UNTER dem
  Datei-Baum, Pro-Datei-Index-Punkte im Baum (indexed/stale/not_indexed/not_source
  mit Legende), Graph-Ansicht (visuell), Verlauf-Modal. Endpoints in
  `handlers/projects.py` (`.../code-index/{status,refresh,rebuild,graph,history}`).
  In den **Externen Bibliotheken** (Allgemeine Einstellungen) gelistet
  (`engine/lib_versions.py`, `_cbm_version()`).

### B. Interaktives Terminal (9.217.0 + .1)
- **Backend** `server_lib/terminal.py`: echtes PTY (`pty.fork` + interaktive
  Shell), Output in Hintergrund-Thread → 256KB-Ringpuffer; **SSE für Output +
  POST für Input** (ThreadingHTTPServer hat KEIN WebSocket; Auth via Bearer-Header
  — NIE Token in URL). `TerminalManager` gekeyt nach (agent,projekt)+sid, max
  8/Projekt, 1h-Idle-Reaper.
- **cwd-Sperre (pragmatisch):** pro Sitzung temp rc-Dir (ZDOTDIR/`.zshrc` für zsh,
  `--rcfile` für bash) lädt echte rc + hängt chpwd/PROMPT_COMMAND-Hook an, der bei
  Verlassen des Projektordners zurückspringt. LIVE verifiziert.
- **Endpoints** `handlers/projects.py`: `.../terminal/sessions` (GET list/POST
  create), `.../sessions/<id>/{input,close}` (POST), `.../sessions/<id>/stream`
  (SSE).
- **Frontend** `web/js/panels_terminal.js`: xterm.js + FitAddon via CDN.
  Bottom-Panel (flex-child von `#main-content`), drag-resizable, **Höhe persistiert**
  (localStorage `terminal-panel-height`). Output via **fetch-Reader** (NICHT
  EventSource — kann keinen Bearer-Header). Toggle-Button in Statusleiste, nur in
  Code-Mode (reuse `_workdirIsCodeChat()`). Verfügbar in Projekt-Ansicht UND
  Code-Mode-Chat; Sitzungen serverseitig pro Projekt wiederverwendet.

### C. Code-Editor im Bottom-Panel + Datei-Vorschau-Modal ENTFERNT (9.218.0)
- Bottom-Panel ist jetzt **gemischter Tab-Workspace** (Terminal + Editor), jeder
  Tab hat `kind:'terminal'|'editor'`.
- Klick auf Datei im Baum → **Editor-Tab** (CodeMirror 5; Modi python/js/ts/json/
  html/xml/css/clike/yaml/shell/go/rust via CDN — **`addon/mode/simple.min.js`
  MUSS vor den Modi laden**, sonst crasht der shell-Modus mit `defineSimpleMode`).
- **Render/Raw-Umschalter** (Render=hljs read-only/Markdown, Raw=CM-Editor),
  **Speichern** (neuer `POST /v1/files/save {path,content}` in
  `handlers/admin_artifacts.py`, `_validate_file_path`, validiert Eltern-Ordner bei
  neuer Datei, 10MB-Cap), **Herunterladen**, ungespeicherte Tabs zeigen ● + warnen.
- **Neue Datei** (Button in Bereichsleiste → rel. Pfad), **Bulk-Close** (Rechtsklick
  → Tab/Andere/Rechte/Alle), **Maximieren** (`#terminal-panel.maximized`).
- **Persistenz serverseitig pro Projekt:** offene Editor-Datei-Tabs + aktiver Tab
  in `project.json → bottom_workspace` (Whitelist `brain.py:~5028`); via
  `_terminalPersist()` (debounced PUT) + `_terminalLoadSessions()` wiederhergestellt.
- Entfernt: das 9.216.0 Vorschau-Modal (`ptShowFilePreview`+`_ptPreview*`+CSS).
  Behalten: `_ptDownloadFile`, `ptDownloadProjectZip`, `_codeIndexShowModal`,
  `_ptLangFor`.

---

## 2. WAS ALS NÄCHSTES ZU TUN IST (der eigentliche Auftrag der neuen Session)

**Nutzer-Wunsch (2026-06-27, noch NICHT gebaut):** den gesynkten Code-Index (cbm)
IM EDITOR nutzbar machen. Das **Maximieren** ist bereits erledigt (Teil von 9.218.0).

Vorgeschlagene Features (mit `code_search`/`code_trace`/`code_query`/`code_snippet`),
Reihenfolge nach Aufwand/Nutzen — **vom Nutzer noch zu priorisieren**:

1. **Symbol-Palette (Cmd/Ctrl-P)** *(empfohlen zuerst, größter aha-Effekt)*: Fuzzy-
   Suche über alle Projekt-Symbole (`code_search`), Auswahl → Editor springt zu
   Datei+Zeile. (Editor öffnen = vorhandenes `terminalOpenFile(abs)`; Sprung zu
   Zeile = CM `cm.setCursor`/`scrollIntoView` — Zeile aus dem cbm-Node holen.)
2. **„Gehe zu Definition" + „Wer ruft das auf?"** (Rechtsklick auf Symbol im
   Editor): Definition via `code_search` (Wort unter Cursor), Aufrufer via
   `code_trace inbound`.
3. **Autocomplete aus dem Index** (CM `show-hint`-Addon, Vorschläge = Projekt-
   Symbole aus `code_search`; rein index-basiert, kein LLM).
4. **Hover-Symbol-Info** (Signatur + „X Aufrufer" + Docstring via
   `code_snippet`/`code_trace`).
5. **Cypher-Suchleiste** (`code_query`, Power-User: „Funktionen ohne Tests" etc.).

**Wie der Editor den Index erreicht:** Die cbm-Tools sind serverseitig; das
Frontend braucht entweder (a) die vorhandenen Agent-Tool-Endpoints/einen neuen
schlanken Endpoint `.../code-index/symbols?q=` (analog zu `code-index/status`,
ruft `code_search`/`code_trace` und gibt JSON zurück), ODER (b) `code_query`
direkt. **Empfehlung:** ein neuer GET-Endpoint `.../code-index/symbols?q=...`
(+ ggf. `?callers=<qname>`) in `handlers/projects.py`, der die cbm-Helfer aus
`engine.tools.codebase_memory` nutzt (es gibt schon `index_status`/`graph_overview`
als Vorlage). Wichtig: cbm liefert `file_path` REPO-RELATIV → mit `working_dir`
zum Absolutpfad für `terminalOpenFile` zusammensetzen (siehe `per_file_state`).

---

## 3. KRITISCHE INVARIANTEN / FALLSTRICKE (unbedingt beachten)

- **NIE SIGKILL** auf brain-agent (`feedback_never_sigkill_brain`). Graceful Restart:
  `launchctl kill SIGTERM gui/$(id -u)/com.brain-agent.server` — **literal `$(id -u)`**,
  der Deny-Hook lehnt var-substituierte Formen ab. Danach `/v1/status`-Version prüfen.
- **brain.py nach CHANGELOG-Edit IMMER py_compile** (`feedback_compile_check_brain_py`)
  — ein ASCII-Quote in dt. Prosa crasht den Boot, js_gate fängt das NICHT.
- **Standalone `python3 -c "import brain"` hat KEINE server_config** (Dual-Modul-
  Footgun) → cbm-Helfer melden „bin missing", lib_versions zeigt „missing". NUR am
  laufenden Server verifizieren (Endpoint/Log/Daemon), nicht via bare import.
- **JS-Gate vor jedem JS-Commit:** `cd web/js && ./js_gate.sh` (braucht Dev-Server).
  Neue Globals → `.globals-count.baseline` anheben (aktuell **1550**). CDN-Globals
  (Terminal/FitAddon/hljs/CodeMirror) sind in `gen-globals.sh` als ambient gelistet.
  Smoke verlangt **0 Konsolenfehler** — fing schon 2 echte Bugs (defineSimpleMode,
  Pfad-Mismatch).
- **Auth'd SSE im Frontend = fetch-Reader, NIE EventSource** (kann keinen Bearer-
  Header) und **NIE Token in der URL**.
- **cbm file_path ist REPO-RELATIV**, folder-tree `n.path` ist ABSOLUT — immer über
  `working_dir` abgleichen (`_ptRelToWorkingDir` im Frontend, `os.path.normpath(rel)`
  im Backend). Symbol-Zählung schließt File/Module/Folder-Wrapper-Nodes aus.
- **Skill + Curated-Changelog mitpflegen** (CLAUDE.md-Regel): user-sichtbare
  Features → `engine/changelog_curated.py` (dt., formelles „Sie", Nutzen-orientiert)
  + `brain_agent_version` in `agents/main/skills/brain-agent-guide/SKILL.md` auf die
  neue VERSION ziehen. Pre-push-Hook prüft beides; rein interne Releases:
  `CHANGELOG_OK=1 git push` bzw. `SKILL_DOC_OK=1 git push`.
- **Direkt auf main committen + pushen** (`feedback_commit_to_main`), keine Branches/PRs
  ohne Aufforderung. Commit-Trailer + Session-Link nicht vergessen.
- **Evals line-buffered** (`feedback_evals_line_buffered`): `print(...,flush=True)`/
  `python3 -u`, sonst hängt der Hintergrund-Log leer.

---

## 4. NÜTZLICHE PFADE & BEFEHLE

- Live-Log: `/Users/alexander/.brain-agent/server.error.log` (launchd routet fd1+fd2 dahin).
- cbm-Binary: `.codebase-memory/bin/codebase-memory-mcp` (CLI:
  `CBM_CACHE_DIR=<dir> .../codebase-memory-mcp cli <tool> '<json>'`).
- Code-Mode-Projekte (Test): `main/qb` (wd `~/Documents/dev/qb`),
  `main/wettervorhersage` (wd `~/Documents/dev/weather`).
- Eval-Harnesses (Regressions-Guard): `eval/codegraph_arena.py`, `eval/codegraph_e2e.py`.
- Schlüssel-Dateien Frontend: `web/js/panels_terminal.js` (Terminal + Editor),
  `web/js/panels_project_tree.js` (Baum, Datei-Klick → `terminalOpenFile`),
  `web/index.html` (CDN-Scripts + Bottom-Panel-HTML), `web/css/main.css` (`#terminal-panel`,
  `.terminal-*`, `.editor-*`, `.ci-*`).
- Schlüssel-Dateien Backend: `server_lib/terminal.py`, `engine/tools/codebase_memory.py`,
  `handlers/projects.py` (code-index + terminal Endpoints), `handlers/admin_artifacts.py`
  (`/v1/files/{preview,download,zip,save}`), `server_daemons.py` (code-index-sync).

---

## 5. OFFENE/BEKANNTE PUNKTE (nicht blockierend)

- **Optik nie per Screenshot geprüft** — Chrome-Extension war in dieser Session nicht
  verbunden. Verifikation lief über js_gate-Smoke (lädt echte Seite, 0 Konsolenfehler)
  + Backend-In-Process-Tests + Live-Endpoint/Daemon. Ein echter Klick-Test (Terminal
  rendert, Tabs, Editor speichert, Maximieren, Persistenz nach Reload) steht noch aus
  → gleich am Anfang der neuen Session sinnvoll.
- `config.example.json` bekommt beim Commit-Hook eine maschinen-absolute `bin`-Pfad-
  Zeile (kosmetisch, ignorierbar).
- Vorbestehende, UNVERWANDTE uncommitted Dateien: `agents/.../corporate.logo.png`,
  `eval/fastcontext_*` — NICHT anfassen, gehören nicht zu dieser Arbeit.
