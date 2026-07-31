---
name: project_quant_workbench_plan
description: "Quant-Workbench (Finance/Simulation/Compliance, Python+SQL+R+Notebooks): Plan QUANT_WORKBENCH_PLAN.md im Repo-Root, Reihenfolge 0→D→B→C→A — ALLE Phasen FERTIG (v9.359.0, Phase A persistente Kernel abgeschlossen); Funde/Betriebswissen hier + im Plan-Log"
metadata: 
  node_type: memory
  type: project
  originSessionId: 43b3fb9c-30b7-4af4-9cc0-1f04ed6551c8
---

**Quant-Workbench** — Claude-Science/Financial-Services-Fähigkeiten für brain-agent, Pivot von
Life-Science auf Banking (2026-07-16). Der vollständige Umsetzungsplan liegt im Repo:
**`QUANT_WORKBENCH_PLAN.md`** (Reihenfolge 0 → D → B → C → A; pro Phase Schritte mit
file:line, Erfolgskriterien, Doku-Pflichten). Eine Umsetzungs-Session liest ZUERST diesen Plan
und pflegt dessen Log-Abschnitt.

**Stand 2026-07-16: ALLE Phasen (0, D1, D2, B, C, A) ABGESCHLOSSEN — Plan komplett (v9.359.0, Commit 3748c575).**
Phase 0 (v9.354.0): `.venv_quant` (Homebrew py3.14, matplotlib…QuantLib 1.43),
`python_exec` venv_path + timeout 120, R 4.6.1, `r_exec`. D1 (v9.355.0): `data_query`
(read-only SQL über .parquet/.csv/.duckdb; DuckDB-Sandbox). D2 (v9.356.0): `db_query` +
`config.json → data_sources` (Boot-Copy in server.py!); Schicht-2-Beweis
ReadOnlySqlTransaction + Abriss-Test. B (v9.357.0): Provenance —
`artifact_versions.produced_by/env_snapshot`, Kwarg-Kette `_after_file_write→
_register_artifact_version→add_artifact_version`, Setzer nur python_exec/r_exec,
UI-Chips (Code klickbar→Skript) im Artefakt-Panel. C (v9.358.0): `.ipynb` = Typ
`notebook` (Rolle output), Renderer-Case (text/html-Outputs NUR im `sandbox=""`-iframe),
`doc_convert._extract_ipynb` (stdlib-json, kein nbformat, nicht markitdown),
write_file-Steering-Satz. A (v9.359.0): persistente Kernel — `engine/kernels.py`
(KernelManager, 1 Kernel/Session, max 3, LRU, Reaper 20 min, stirbt mit Brain) +
`kernel_exec`/`kernel_status`/`kernel_restart` (Gruppe code_exec, interactive-only:
Purpose-Seed + fail-loud-Guard gegen sched-*/bg_task) + Kernel-Badge
(`kernel_badge.js`, +1 Global, Baseline 2007) + SSE `kernel_status` +
`/v1/kernel/{status,restart}`.

- **A-Funde (Betriebswissen)**: (a) jupyter_client MUSS im SERVER-Interpreter liegen
  (Homebrew py3.14, --break-system-packages), ipykernel in `.venv_quant`; Kernelspecs
  werden pro Boot nach `.venv_quant/brain-kernelspecs/` generiert (brainpy =
  Server-Interpreter + PYTHONPATH=venv, brainr = IRkernel). (b) Interaktiver Chat-Stopp
  erreicht laufende Tools NICHT (Loop pollt nur zwischen Runden) — kernel_exec pollt
  deshalb den ask_user-Cancel-Seam (`_ask_turn_cancelled`) → Interrupt, Zustand überlebt;
  2-stufige Eskalation über `cancel_escalate`-Handles in `kill_tool_process`.
  (c) `user_expressions` bei silent=True leer → silent=False + store_history=False.
  (d) SessionManager.delete beendet den Session-Kernel sofort (verwaister Kernel war
  Live-Fund). (e) MLX-Konflikt irrelevant: Kernel = eigener OS-Prozess.

- **D1-Fund (DuckDB-Lockdown, gilt für A)**: `enable_external_access=false` allein
  bricht Lazy-Views — richtig ist `allowed_paths`=Eingabedateien → external_access=false
  → Views → `lock_configuration=true`; `.duckdb` MUSS davor READ_ONLY-attacht werden
  (WAL-Sidecars). Datei-Kappe bewusst 512 MB statt „30 MB wie xlsx_query" (dort
  SQLite-Materialisierung, hier Streaming); Ergebnis-Kappe 200k. data_query IST in
  GDPR_ARGS_DEANON_TOOLS + _WORKFLOW_STEP_TOOLS (Parität xlsx_query, anders als r_exec).
- **D2-Funde**: (a) `scripts/scrub_config.py` redigierte `dsn` NICHT — DSN-Passwörter
  wären via pre-commit-Refresh in config.example.json geleckt; Marker `dsn` +
  Platzhalter ergänzt (gilt für JEDEN künftigen Config-Key mit Credentials: Marker
  prüfen!). (b) psycopg2: Default-Cursor lädt das GANZE Resultset client-seitig —
  db_query nutzt named (server-side) Cursor; `description` erst NACH erstem fetch
  lesen. (c) Nur type=postgres verdrahtet; mssql/snowflake/oracle = fail-loud, Nachrüsten
  in `_connect_readonly`. (d) db_query NICHT in _WORKFLOW_STEP_TOOLS (deny-by-default).
  (e) Test-Infra per-Maschine: brew postgresql@17 (Service läuft), DB `braintest`
  (Tabelle positionen 50k, Role brain_ro/brain_ro_test), psycopg2-binary 2.9.12 im
  Homebrew-Python; config.json data_sources[braintest] zeigt darauf.

Nicht-offensichtlicher Kontext, der nicht im Plan-Text ableitbar ist:
- **User-Entscheidungen** (verbindlich, 2026-07-16): alle drei Datenquellen (Warehouse + Uploads
  + Parquet), alle drei Zielgruppen (Notebook = Werkzeug UND Prüfartefakt UND Bericht),
  R = echter Bedarf (bestehender R-Code), Start = Environment.
- **Phase-0-Funde** (Details im Plan-Log): `python_exec` stand global auf
  `tool_settings.states.interactive=inactive` (Alt-Seed der Tool-Matrix) — Modelle wichen auf
  execute_command aus; per POST /v1/tools/settings auf active gesetzt (config.json,
  per-Maschine, NICHT im Repo). Neue Tools werden beim Boot automatisch interactive-active
  geseedet. r_exec bewusst NICHT in `GDPR_ARGS_DEANON_TOOLS` (Local-Safe-Check parst kein R)
  und NICHT in `_WORKFLOW_STEP_TOOLS`.
- **ABI-Falle Phase 0** (erledigt, gilt weiter bei venv-Rebuilds): `venv_path` wird als
  PYTHONPATH injiziert, der Interpreter bleibt `sys.executable` = Homebrew-Python 3.14 →
  `.venv_quant` MUSS mit `/opt/homebrew/bin/python3` gebaut werden.
- **Schon installiert** (nicht doppeln): duckdb 1.5.2, pyarrow 24.0.
- Positionierung: „Notebooks, die durch die Modellvalidierung kommen" (BCBS 239/MaRisk) —
  der Burggraben ist der bestehende Compliance-Stack, nicht die KI.
- Studien-Artefakte: Finance-Version https://claude.ai/code/artifact/4576b14c-dcd8-419b-896a-0cdd3c35acd8,
  Science-Vorläufer https://claude.ai/code/artifact/89bcf755-edd2-433b-82a1-82b4b7d80b9d.
- Muster-Verwandtschaft: wire-only-Seam + Phasenvorgehen wie [[project_design_mode_phase_a]];
  SELECT-only-Modell aus [[project_xlsx_toolset]].
