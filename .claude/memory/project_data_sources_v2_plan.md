---
name: project_data_sources_v2_plan
description: "Umsetzung DATA_SOURCES_V2_PLAN.md: ALLE 9 Phasen committed (MSSQL 9.368, ro/rw 9.369, Scoping 9.370, Projekt 9.371, Session 9.372, REST 9.373, Steckbrief 9.374, Datensparsamkeit 9.375, Docs Phase 9 — live verifiziert). Offen nur noch: MSSQL-Live-Test gegen echten Server (O4)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 01b88cd9-fd3b-4b03-90b7-3819bba45dca
---

**Datenquellen v2 — KOMPLETT (2026-07-17, Phasen 1–9).** Plan:
`DATA_SOURCES_V2_PLAN.md` im Repo-Root. Server auf 9.375.0.

**Phase 8 (9.375.0) — Datensparsame Kette, live E2E verifiziert:**
`context_preview none|head|full` pro Quelle (`_effective_preview` —
Tool-Param `preview` kann NUR verschärfen; none = nur {columns, row_count},
kein GDPR-Pass nötig); Parquet-Export `out='x.parquet'` für db_query
(`_write_parquet`, pyarrow, per-Spalte String-Fallback); Kette in der
Tool-Prosa (db_query/data_query/rest_query). BEOBACHTUNG: none erzwingt die
Export-Kette auch für information_schema-Erkundung (Metadaten-Zeilen sind
Zeilen) — Modell exportiert die Schema-Liste und liest sie via data_query;
bewusst KEINE Ausnahme. Live-E2E (glm-5.2 Cloud): strict-Quelle → Aggregat →
summe_pro_filiale.parquet → data_query → render_diagram; kein 'DE0000' in
irgendeinem tool_result (10 Calls).

**Phase 7 (9.374.0) — Quellen-Steckbrief, live messbar verifiziert:**
`guide {md, skill?, auto_generated_at?}` pro Quelle;
`build_data_source_guide_preamble` wire-only auf der letzten User-Message
(Websuche-Seam in handlers/chat.py, `_ds_guide_cache` neben _goal_web_cache;
NICHT GDPR-geseamt — admin-authored wie Design-Preamble). Kappe
`data_sources_guide_max_tokens` (Default 4000, Boot-Copy server.py) über
die SUMME: darüber pro Quelle Kurzzeile use_skill('<skill>'); md-only groß →
2000-Zeichen-Slice mit Gekürzt-Marker; md-only klein → trotzdem voll.
generate_guide (Admin-POST-Action) = DETERMINISTISCHER Schema-Bootstrap
(pg_class-reltuples/sys.partitions + information_schema + FKs; REST:
Skelett aus allowed_paths, kein LLM). GOTCHA: ro-Postgres-Exec-Cursor aus
_connect_readonly ist NAMED (single-use) — Metadaten-Queries auf frischen
plain Cursors. MESSUNG live (glm-5.2, „Gesamtwert Stück×Kurs Filiale 3"):
MIT Steckbrief 1 db_query (direkt korrekt), OHNE 3 Calls (2
information_schema-Runden). GET /messages bewies: kein Steckbrief-Text in
der DB.

**Phase 9 — Docs komplett:** Skill 01-api (data-sources-Endpoints inkl.
generate_guide/available/tables), 02-tools (db_query-Block neu: 4
Guard-Achsen + rest_query-Block), 04-recipes (MSSQL/rw/REST/Steckbrief/
Datensparsamkeit/OnBase-Rezept), 05-internals (Scope+Preamble-Mechanik),
06-user-manual (DE: Right-Panel-Tab, Projekt-Sektion, Admin-Tab),
SKILL.md 1.237.0/9.375.0. Kuratierte Einträge: user (Quellen in 2 Klicks,
9.370–9.374) + admin (MSSQL/REST/rw/Datenschutz, 9.368–9.375).

**Phasen 1–6 (9.368–9.373):** siehe DATA_SOURCES_V2_PLAN.md + technischer
CHANGELOG (brain.py). Kernfakten: MSSQL NUR pyodbc + „ODBC Driver 17"
(SERVER=host,port KOMMA, OHNE Encrypt, NIE Driver 18 — Anhang B);
msodbcsql17 MANUELL installiert (brew list kennt es nicht); Scope-Gate im
Tool (Policy → Scope → Modus → Tabellen, deny-by-default, __system__-Bypass);
Tabellen-Whitelist sqlglot (CTE-safe, information_schema/sys frei,
fail-closed); rest_query base_url-Confinement (SSRF-frei,
follow_redirects=False); ro-DML-Kurzschluss MUSS mode-gegated sein
(named-Cursor description=None vor erstem Fetch).

**OFFEN:** MSSQL-Live-Test gegen echten SQL Server (O4 — Suite
TestDbQueryMssql skippt bis Docker/DSN da ist; Treiber-Stack installiert,
`scripts/setup_mssql_testdb.py` liegt bereit). Lokale Test-Ressourcen:
braintest (Postgres, 50k positionen — id/filiale_id/isin(DE0000…)/stueck/
kurs, SUM stueck 12525000), braintest_rw (Owner-DSN, rw); braintest trägt
jetzt einen generierten Steckbrief in config.json.

Verwandt: [[project_quant_workbench_plan]], [[feedback_kv_cache_stability]],
[[feedback_prompt_bloat_regression]], [[project_windows_deployment_package]]
(pyodbc/Driver-17-MSI im Win-Bundle).
