# Agent Tool Reference

Every tool the LLM can call in a chat turn. Names match the actual
`tool_use` block. Dispatch path (in-process since v9.247.0): the loop
(`engine/llm_loop.py`) parses a `tool_call` from the stream and calls
`engine.TOOL_DISPATCH[name](args)` directly on its own thread (or the MCP
fallback) → result returned to the model as a `role:"tool"` message.

Tools are gated per-call by a 3-layer resolver, and the status is now
settable **per use-case** (purpose):
1. Global status + purposes (admin-edited `config.json → tool_settings`).
   Each tool has ONE scalar status, `state ∈ {active, inactive, deferred}` —
   active = in prompt · inactive = off · deferred = tool_search-only.
   The scalar `state` is the **catch-all default for every purpose**.
   An optional `states: {<purpose>: state}` map sets the status independently
   per channel; any purpose NOT in the map inherits the scalar default.
2. Per-agent override (`agent.json → token_config.tool_overrides.<name>`):
   `{states: {<purpose>: state}}` (or a legacy scalar `{state}`). A purpose
   entry here REPLACES the global state for that purpose; absent = inherit.
3. Call purpose / use-case: `interactive | transform | memory_summary |
   research_minimal | helpdesk`.

Per-purpose resolution is `resolve_tool_state_for(name, agent_id, purpose)`
(agent purpose → agent scalar → global purpose → global scalar → 'active').
`resolve_active_tools` applies it uniformly across ALL purposes: the per-purpose
base sets (interactive = agent's allowed set; memory_summary / research_minimal /
helpdesk = their fixed sets) are now **defaults** — a tool set `active`/`deferred`
for a purpose it isn't in is ADDED; one set `inactive` is REMOVED. This is
guarded by a no-op fast path: when no tool carries a `states.<purpose>` entry,
the channel's surface is byte-identical to before (preserving the warm-pool KV
prefix for `interactive`).

**Two editing surfaces.** General Settings → Tools shows a GLOBAL matrix: every
tool row carries a status dropdown per use-case (Chat · Transform · Memory ·
Research · Brainy) plus a per-channel status summary (active/inactive/deferred
counts + realized token size of the tool injection). The expanded tool panel's
single "Standard (alle Zwecke)" dropdown edits the scalar default. Agent
Settings → Tokens shows the per-agent override matrix (currently the Chat /
`interactive` column only — the resolver supports the rest, the UI exposes one).

Brainy (the helpdesk bot) runs with `purpose='helpdesk'` and a fixed read-only
tool set BY DEFAULT — see "Helpdesk tools" below. That set is now a default an
admin can extend/restrict via the helpdesk column: adding a write/exec tool there
makes Brainy able to write/run (the global matrix warns ⚠ on this). Since 9.22.0
the resolved tool names are enforced at dispatch: `tool_mcp` rejects any
`tool_use` not in the turn's allowed list before it runs. That allowed list is
the DISPATCHABLE set = **active ∪ deferred** (NOT in-prompt only) — so a deferred
tool the model reaches for (directly or after `tool_search`) RUNS; only hard-
EXCLUDED tools (Websuche web-lockout, helpdesk read-only) are rejected. (Before
9.131.0 the whitelist was in-prompt-only, so a deferred tool was wrongly rejected
'not available in this context' — deferred collapsed to disabled, e.g. read_document
on an attachment turn that the classifier had deferred; chat f2168652.)

Deferred tools are hidden from the initial list, dispatchable, and surfaced via
`tool_search`. Disabled tools are neither in-prompt, tool_search-able, nor
dispatchable (never in the base set). (The tool_search half of that sentence
was drift until 9.276.1: discovery searched the raw TOOL_DEFINITIONS catalog
with no state check, so a globally-inactive tool was still advertised with
full schema — chat 2cb5a9dd, glm-5.2 hunted the disabled exa_search for a
whole turn. tool_search now filters by the turn's dispatch whitelist, so
discovery ⇔ callability.)

**Discovery becomes DECLARATION (9.277.0).** A `tool_search` hit is (a)
re-declared on the wire mid-turn — `run_loop`'s `tools_refresh` hook rebuilds
the tool array + dispatch whitelist at the next round boundary when a new name
was discovered (event `tools_redeclared`), so strict function-callers (glm)
that only ever call declared tools can use the find in the SAME turn — and
(b) persisted per SESSION (`session._discovered_tools`, seeded onto the
request context each turn, merged back at turn end), so later turns declare it
from the start. This also survives the cache-priced turn-1 tool freeze: the
resolver's discovered-exemption applies to the union of static deferral and
the frozen classifier trim. Monotonic growth only — one prefix-cache miss per
discovery, then byte-stable again.

**Two layers reach the model: the wire schema and the admin prose overlay.**
Each tool's wire schema (the `description` + `input_schema` on the `tools` array)
defaults to code (`engine/tool_schemas.py → TOOL_DEFINITIONS`).
- **Wire description — now editable** (v9.101.4): `config.json →
  tool_settings.<tool>.wire_description` overrides the code description. When set,
  `_filter_tools` (the single seam every purpose + warmup path uses) swaps it onto
  the wire dict the model receives (shallow-copying only overridden tools, so
  TOOL_DEFINITIONS is never mutated and non-overridden tools stay KV-stable).
  Empty = code default. Edited in the per-tool ⚙ config modal ("Beschreibung
  (Wire — editierbar)" + reset-to-default). GET /v1/tools/settings exposes
  `wire_description_code` / `wire_description_override` / effective
  `wire_description`.
- **`input_schema` stays read-only** — bound to the tool's Python signature; the
  modal shows it (param table + raw JSON) for verification only.
- **Admin "Prompt-Text" overlay** (description / when_to_use / warnings / examples
  in tool_settings) is SEPARATE from the wire schema: rendered as a `## <tool>`
  block appended to the system prompt by `_render_tool_descriptions` (gated by
  `applies_with`) — additional guidance layered on top, not the wire schema.

Editing the wire description (like a prose edit) changes the system-prompt tool
array, so the warm-pool KV prefix desyncs until the next warmup rebuild (no
explicit invalidation is wired — a one-off latency cost on the first turn after).

## Core file ops

- `read_file(path, start_line?, end_line?)` — read text file, optional line range
- `write_file(path, content, mode?)` — create/overwrite; relative paths land
  in the session's artifact folder. Writes are HARD-RESTRICTED to that folder:
  an absolute path or a relative `..` escape that resolves outside it is REFUSED
  with an error (v9.153.0). Same restriction on `write_document`. (CLI/warmup
  with no session: unrestricted fallback.)
- `edit_file(path, old_string, new_string, replace_all?)` — exact-string edit.
  **Rescue (9.309.0)**: when the exact match finds nothing, tolerant passes
  run — typographic normalization (curly quotes/dashes/nbsp/zero-width) and
  whole-line matching with trailing-whitespace tolerance + a uniform indent
  delta (new_string is re-indented by that delta). A UNIQUE tolerant match is
  applied (result carries `rescued: <mode>` + a note); an ambiguous one errors.
  Edits that matched exactly before behave byte-identically.
  **Rescue 3 (9.353.2, `anchor-span`)**: for LONG old_strings (≥200 chars)
  whose MIDDLE the model garbled — opaque encoded blobs like data-URI SVGs /
  base64 images (the design-mode hero case). The first/last 32 chars must
  match verbatim as anchors; the span between them is replaced only when its
  length is within ±35% of old_string AND difflib similarity ≥0.80, so a
  mis-anchored pair never swallows unrelated content. Same ambiguity contract.
- `list_directory(path, recursive?)` — ls
- `search_files(root, pattern, ...)` — grep / find
- `execute_command(cmd, cwd?, timeout?)` — shell. NO TTY, no stdin,
  `TERM=dumb`. Banned commands (sudo, rm -rf /, …) rejected.

## Document ops (binary-friendly)

- `read_document(path, ...)` — auto-routes by extension: PDF→markdown
  (pymupdf4llm default), docx/pptx, xlsx/**xlsm**/xls/**xlsb** (every sheet as a
  markdown table; `sheet=` selects one; **VBA macro source** is appended as
  ```vba blocks — never executed), csv/tsv, eml/msg, epub/zip, images. Honors the
  `.md` companion in `<dir>/.brain-extracted/<name>.<ext>.md`. Returns content
  VERBATIM (no size cap — only the model context limits a big read). Use this for
  any non-`.txt` attachment.
- `write_document(path, content, format, style?)` — produce docx/pdf/pptx/xlsx
  from markdown; embeds `![alt](file)` images (docx/pptx/pdf) → pair with
  render_diagram for reports/slides with diagrams. Formats: **.docx/.xlsx/.pptx/
  .pdf/.html** (html = self-contained styled web report, images inlined as
  base64 — ALWAYS use write_document, not write_file, for HTML reports so the
  preset is applied). `style=<preset>` applies an editable style (fonts/colors/
  layout + running header/footer/logo) from `agents/<agent>/skills/doc-styles/
  <preset>.yaml` (e.g. `corporate`) — deterministic, model just writes markdown.
  A DEFAULT preset applies even when style= is omitted (project `doc_style` →
  config `doc_styles.default` → `corporate` → built-in), so output is on-brand by
  default (v9.154.0). **Match a reference/template instead of a preset**: pass
  `style='reference'` (auto-picks the current project's instruction-file `.docx`)
  or `style='reference:<filename>'` — Brain reads that `.docx`'s real fonts /
  heading styles / colors / margins (incl. the Word `docDefaults` body font) in
  code and applies them, bypassing the brand preset. Use it when the user wants
  output "im Format von / wie die Referenz" (.docx output only; lifts named-style
  + margin definitions, not the full visual template/themes) (v9.190.0).
  **Editorial report layout (.html only) — the default for any HTML report**:
  pass `style='report'` (alias `'editorial'`) to render an `.html` file with the
  SAME polished magazine-style layout Deep Research uses — warm editorial palette,
  drop-cap intro, gradient-underlined headings, sticky table-of-contents sidebar,
  collapsible sources, light/dark + print-ready. As of v9.260.0 this is the RIGHT
  DEFAULT for ANY HTML report request (e.g. "erstelle einen html-report", "due
  diligence report als HTML") — not only when the user says the word "schön/nice";
  the plain doc-styles preset (Calibri look) is the fallback only when a specific
  on-brand letterhead is explicitly required. The model writes plain markdown (the
  first `# Heading` becomes the report title) — do NOT hand-write HTML for this
  (raw HTML can't be re-flowed into the layout, and falls back to the normal
  preset) (v9.249.0, default-widened v9.260.0). **Hero image** (v9.287.0, analog
  Deep Research): optional `hero_image=<https-URL>` puts a real lead image
  full-width under the report headline — the model should pass one it saw during
  research (og:image / article lead photo). If omitted, Brain auto-tries the
  og:image of the first links cited in the content, then (v9.287.1) of the
  HTML pages web_fetch fetched earlier in the SAME turn (≤4 candidates total);
  only when nothing is found does the generated abstract SVG banner appear.
  Header/footer text supports `{page}`/`{date}` tokens; the logo + footer render
  on docx/pdf pages, pptx slides, and the html header/footer bands. **Automatic
  footer lines** (v9.208.0, each on its own line, in the footer font): a
  content-derived **classification** `Klassifizierung: <Stufe>` (Öffentlich/Intern/
  Vertraulich/Streng vertraulich, from the ARL heuristic over the document text,
  no LLM; business reports floor at Intern), an **EU-AI-Act AI-generation
  disclosure**, and the **page number** as `Seite - N` (own line, same font/size).
  All three are added automatically — `footer.text` is for manual content only;
  toggle via `footer.{classification,ai_disclosure,page_numbers}` in the preset. A
  header logo auto-clears the first heading (top margin pushed down).
  **Automatic .docx polish** (v9.191.0, deterministic, every model): cover page
  (from the first `# H1` + leading `Key: value` lines) + table of contents for
  substantial reports — the TOC is a **native Word field** that Word fills with
  correct page numbers on open (v9.209.0; the v9.207.0 pre-filled-PAGEREF variant
  showed every entry as page 1 in Word and was reverted), dark table headers +
  zebra rows, `---` → real divider,
  leading emoji stripped from headings, inline `**bold**`/`*italic*` parsed in
  headings AND table cells (no longer leaked verbatim), and **colour-coded risk
  badges** — a table column of gering/mittel/erhöht/hoch ratings is auto-shaded
  green/amber/red. **Page-break hygiene + layout** (v9.207.0): headings stay with
  their text (no stranded heading at a page bottom), table rows never break across
  a page and the header row repeats on each page, and table columns are sized to
  their content so a narrow column no longer wraps one character per line. A
  section headed **`## Versionshistorie`** (or `## Änderungshistorie`) starts on a
  fresh page and is left out of the TOC — use it for a change-history table at the
  document end. The model triggers ONE feature explicitly: `::kpi VALUE | LABEL |
  risk` lines render a coloured KPI stat-box strip. All toggled by `docx.{cover,
  toc,zebra_fill,rule_color,strip_emoji,risk_badges}` in the preset.
  **Markdown engine** (v9.192.0; pptx parity v9.192.2): the .docx/.pdf/.pptx body
  is parsed with markdown-it-py (a real CommonMark parser), so standard markdown
  converts NATIVELY — nested bullet/ordered lists, `> ` blockquotes, fenced code
  blocks (monospace + shaded), and `[text](url)` clickable links (all previously
  dropped as raw text by the old line parser). All THREE formats share the same
  block model + renderer features (badges/zebra/inline). pptx: `# `/`## ` start
  slides, bodies get bullets/nested lists/real tables-with-badges/inline runs.
  ::kpi + cover frontmatter are peeled off in a pre-pass before markdown-it; OUR
  renderer (not the lib) still owns the look. The `document-markdown` skill tells
  the model how to write converter-friendly markdown; regression-guarded by
  `eval/doc_render_eval.py` (docx + pdf + pptx asserts).
- `edit_document(path, ...)` — structural edit (single XLSX cell/row, DOCX
  replace_text, PPTX slides). For anything bigger on spreadsheets → `xlsx_edit`.

## Spreadsheets — deterministic XLSX toolset (v9.262.0, extended v9.263.0)

WHY: spreadsheet quality used to depend on the chat model writing pandas/
openpyxl code via python_exec — strong models managed, weak/local ones churned
and delivered CSV instead of styled workbooks (chats 2cb94154 vs 98cceac2).
These four tools make the model supply only INTENT (a SQL SELECT, a small JSON
spec); the server moves the data deterministically. **Bulk data never flows
through the model** — like the docx/html pipeline where the model writes
markdown and code renders the file. Impl: `engine/tools/xlsx_tools.py`.

- `xlsx_inspect(path|paths, sheet?)` — workbook profile WITHOUT reading data
  into chat: sheets, real dimensions (dead "Spalte<N>" placeholder columns
  trimmed), detected header row, per-column name/type/nulls/distinct/min-max/
  samples, merged cells, formula count, named ranges, **join-key candidates**
  across sheets (same-named columns with value overlap → "likely JOIN key"),
  and a copy-paste `Tables for xlsx_query:` block with the exact sanitized
  table/column names. ALWAYS the first call of any spreadsheet task.
- `xlsx_query(path|paths, sql, out?, sheet?)` — ONE read-only SQL SELECT over
  the sheets, each loaded as a SQLite table (in-memory, per call). JOINs across
  sheets, GROUP BY, filters — no code. Returns first 50 rows + total count;
  `out='name.csv'` writes the FULL result to the artifact folder. With
  `paths=[a, b]` tables get file-stem prefixes (`orders_alt_orders` …) for
  cross-FILE comparisons. SELECT-only is triple-enforced (prefix check,
  sqlite3 authorizer, `query_only=ON`); SQL errors echo the full schema so the
  model self-corrects in one round.
- `xlsx_create(path, spec)` — declarative JSON spec → styled workbook (header
  fill/bold from the doc-style preset, freeze panes, auto column widths,
  number formats `text|int|number|eur|percent|date`, banded rows, `totals`
  as real `=SUM()`). Data per sheet: inline `rows` (small only, ~5k-cell cap),
  `source:{file, sheet?, sql?}` (server-side flow — the model transcribes
  ZERO rows), or `master_detail:{key, master:{source, columns?}, detail:{…}}`
  (grouped master→detail layout: tinted master row with bold key, detail rows
  beneath with outline grouping — the marktorder case). Plus `charts:[{type:
  bar|line|pie, labels, series, title?}]` and `conditional:[{columns, rule:
  color_scale|data_bars|{lt|gt|eq, fill}}]`.
- `xlsx_edit(path, spec)` — change an EXISTING workbook (artifact folder only)
  preserving formatting/formulas; header assumed in row 1. Ops: `append_rows`
  (inherits last row's style; `source` pulls rows server-side), `add_column`
  (`formula:"=B{row}*C{row}"` filled down), `update_cells` (where equals/
  contains/lt/gt → set), `add_sheet` / `rename_sheet` / `delete_sheet`,
  `set_format`. `.xlsm` keeps VBA (`keep_vba`).

`write_document` .xlsx also renders through the same engine now (markdown
tables → styled sheets), so ALL xlsx output shares one renderer.

**v3 additions (v9.264.0):**
- **xlsx_diff v2**: composite keys (`key='KUNDE,DATUM'`), `compare='formulas'`
  (diffs formula strings — finds edited/broken formulas even when values
  match), and `out='diff.xlsx'` = a HIGHLIGHTED workbook (changed cells
  yellow + old value as cell comment, added rows green, removed rows red
  appended at the bottom).
- **Pivot layout**: sheet with `pivot:{rows, cols?, values, agg: sum|count|
  avg|min|max}` + source → deterministic cross-tab with Gesamt row.
- **Charts v2**: types + `area`/`scatter` (labels = X values), `stacked:true`
  (bar/area/line), `secondary:[col]` → combo chart with right-hand Y axis.
- **Recalc**: `recalc:true` on xlsx_create/xlsx_edit runs a headless
  LibreOffice round-trip so formula VALUES are computed immediately (openpyxl
  writes formulas but never calculates; without recalc a follow-up xlsx_query
  sees NULLs). Needs soffice (auto-detected; config `xlsx.soffice_path`).
- **Legacy formats**: `.xls`/`.ods` are readable everywhere (inspect/query/
  diff/create-source) via a cached soffice conversion (`/tmp/brain-xlsx-convert`,
  mtime-keyed).
- **Big files**: >30MB workbooks load when `sheet=` is given (streaming);
  sheets >50k rows render on a fast path (styled header/freeze/widths, no
  per-cell styling) with `tool_progress` events.
- **UI**: the grid preview got client-side SORT (header click), SEARCH,
  column resize, and INLINE EDITING (dblclick a cell in a bottom-panel
  xlsx tab → writes via `POST /v1/files/xlsx-cell`, mtime conflict check);
  JSON/XML render as the collapsible data tree in the right panel too
  (attachments + artifacts; the Quelltext toggle keeps the raw view).
  v9.265.0: cell editing ALSO in the right panel's Dateien tab (artifacts =
  agent outputs; attachments stay read-only — they're model inputs), and
  .xlsm files show their **VBA module sources** as ⚙-tabs in the grid viewer
  (`GET /v1/files/xlsm-vba`, oletools, read-only + .bas export; macros never
  execute — writing VBA back into vbaProject.bin needs Excel, so there is
  deliberately NO save).

**v4 additions (v9.266.0):**
- **Streaming writer**: table sheets beyond 100k rows switch the WHOLE
  workbook to openpyxl write_only mode (constant memory instead of GBs) —
  styled header/freeze/widths/totals kept, banded rows/number formats/
  charts/conditional skipped (result carries `mode:"streaming"` + note).
  Create sources may load up to 750k rows (query display stays capped 200k).
  master_detail/pivot sheets can't stream — mixing one with a huge table
  errors with a fix hint.
- **`compare='formats'` in xlsx_diff**: diffs per-cell FORMAT signatures
  (number format, bold/italic/underline, font/fill colour) while rows are
  still matched by their VALUE key — finds re-coloured/re-formatted cells
  with identical values. Works with out='diff.xlsx' (old signature in the
  cell comment).
- **Undo im Edit-Grid**: every saved cell edit lands on an undo stack; the
  ↩-Rückgängig button (with count) writes the previous value back via the
  same /v1/files/xlsx-cell path. Per grid view, newest first.
- **Project profiles**: project-sync files one structure profile per project
  spreadsheet (`<pdir>/xlsx-profiles/xlsxprofile-<hash>.md`, mtime-gated,
  stale-pruned, mined into the wing, NO KG pass) — "welche Datei hat Spalte
  X?" answers from mempalace_query without a live inspect.

**v2 additions (v9.263.0):**
- **Reading robustness**: multi-table sheets split into one table per block
  (`Report`, `Report_2`, …; split on ≥2 blank rows + header-ish next row);
  merged two-row headers compose to `Q1 / Umsatz` column names (merges parsed
  straight from the xlsx zip — `_read_merges`, read_only can't see them).
- **`xlsx_inspect deep=true`**: data-quality audit — duplicated rows, numeric
  outliers (3×IQR), orphan join-key values (present on one side only), and a
  formula map (top formula patterns per sheet + which sheets reference which).
- **Pipeline handles**: `xlsx_query save_as='x'` stores the full result
  per session; `result:x` is then a valid path/source.file in xlsx_query,
  xlsx_diff, xlsx_create and xlsx_edit (in-memory, max 8/session, 500k cells).
- **`xlsx_diff(path_a, path_b, key?, sheet?, out?)`** — 5th tool: deterministic
  workbook compare; with `key` a keyed row diff (added/removed/changed with
  per-cell old→new), without positional; detail capped at 50 lines,
  `out='diff.csv'` saves the full change list.
- **xlsx_create extras**: `spec.template={file}` copies a styled corporate
  workbook and writes ONLY data at `anchor`/`named_range` (template styling/
  formulas untouched); `master_detail.subtotals:[col]` (=SUM row per group);
  `autofilter`; column `choices:[...]` (dropdown data validation); `print:
  {orientation, fit_width, repeat_header}`.
- **UI grid preview**: xlsx/xlsm click in the file tree opens an in-app table
  grid (bottom panel, sheet tabs; right-click still offers extern öffnen);
  csv/tsv "Ansicht" mode renders the same grid; artifacts fullview previews
  xlsx instead of the download-only card. Endpoint `GET /v1/files/xlsx-grid`.

**v5 additions (v9.318.0) — JSON/XML in der Grid-Pipeline + text_diff:**
- **JSON/XML als Tabellen**: `.json`/`.jsonl`/`.ndjson`/`.xml` laden in dieselbe
  Grid-Pipeline wie xlsx/csv — damit sind **alle fünf xlsx-Tools format-agnostisch**
  (inspect profiliert einen JSON-Export, query joint CSV gegen JSON, diff
  vergleicht CROSS-FORMAT z. B. alte CSV gegen neuen JSON-Export, create macht
  aus XML ein gestyltes Workbook). Mapping: JSON-Record-Array = eine Tabelle
  (verschachtelte Objekte flatten zu `a.b`-Spalten, Lookup-Dicts `{id:{...}}`
  bekommen eine `_key`-Spalte, Skalar-Reste landen in `<stem>_meta`); XML:
  jedes wiederholte Element (≥2 Geschwister) = eine Record-Tabelle (Attribute +
  Leaf-Kinder; XML-Text wird wie CSV zahlen-koerziert, JSON bleibt nativ
  typisiert). `compare='formulas'/'formats'` verweigert Nicht-XLSX sauber.
- **`text_diff(path_a, path_b, mode?, context?, out?)`** (documents-Gruppe,
  `engine/tools/diff_tools.py`): deterministischer **unified Diff** beliebiger
  TEXT-Dateien (Code, Configs, SQL, Logs) — Zähler (+/-/Hunks/Ähnlichkeit),
  Binär-Guard (verweist auf xlsx_diff), 10-MB-Kappe. `mode='json'` = 
  **struktureller** JSON/JSONL-Vergleich (Pfad→Wert: added/removed/changed;
  Objekt-Key-Reihenfolge egal, Array-Position zählt — richtig für verschachtelte
  Configs, wo ein Zeilen-Diff in Umsortier-Rauschen ertrinkt). `out='diff.html'`
  speichert eine Side-by-Side-Gegenüberstellung als Artefakt, `.txt`/`.diff`
  den rohen Patch. Abgrenzung: TABELLARISCHE Daten → `xlsx_diff` (matcht
  Zeilen per Schlüssel), Text/Code → `text_diff`.

**data_query — SQL über Parquet/CSV/DuckDB (v9.355.0, Quant-Workbench D1):**
- **`data_query(path|paths, sql, out?)`** (documents-Gruppe,
  `engine/tools/data_tools.py`): EIN read-only SQL SELECT (DuckDB-Dialekt)
  **direkt gegen `.parquet`/`.csv`/`.tsv`/`.duckdb`-Dateien** — der
  Columnar-Pfad für GROSSE Datenextrakte. Anders als xlsx_query (lädt Zeilen
  in In-Memory-SQLite) scannt DuckDB die Dateien **lazy über Views**: ein
  GROUP BY über 1 Mio. Parquet-Zeilen läuft in Millisekunden, nichts wird
  vorab geladen. Jede Datei wird eine View mit dem Datei-Stem als Namen
  (Tabellen in einer `.duckdb` behalten ihre Namen); **jedes Ergebnis listet
  die Views mit Zeilenzahlen**, ein SQL-Fehler echot das volle Schema
  (Selbstkorrektur in einer Runde — es gibt bewusst KEIN data_inspect).
  Cross-Format-JOINs (parquet × csv) funktionieren. Anzeige 50 Zeilen +
  row_count; `out='name.csv'` schreibt das volle Ergebnis (Kappe 200k
  Zeilen) als Artefakt. **Read-only dreischichtig**: (a) derselbe
  SELECT/WITH-Prefix-Check + Multi-Statement-Reject wie xlsx_query
  (importiert, nicht kopiert), (b) Quellen sind Views bzw.
  READ_ONLY-attachte DBs, (c) Engine-Lockdown — `allowed_paths` = exakt die
  Eingabedateien, `enable_external_access=false`, `lock_configuration=true`;
  COPY TO und Zugriffe auf ANDERE Dateien scheitern in DuckDB selbst.
  Datei-Kappe 512 MB (Sanity-Bound; bewusst über xlsx_querys 30 MB — dort
  wird materialisiert, hier gestreamt). Abgrenzung: `.xlsx/.json/.xml` →
  `xlsx_inspect`/`xlsx_query`; `.parquet/.duckdb` (oder riesige CSVs) →
  `data_query`.

**Jupyter-Notebooks (.ipynb) als Artefakt (v9.358.0, Quant-Workbench C):**
- `.ipynb` ist erstklassiges Artefakt: `_ARTIFACT_TYPE_MAP` → Typ `notebook`,
  Rolle `output` (Prüfartefakt, NICHT intermediate). Das Artefakt-Panel
  rendert die Zellen: Markdown via `renderMarkdown`, Code via hljs (Sprache
  aus `metadata.kernelspec.language`), Outputs `image/png` als `<img data:>`,
  `text/plain`/`stream` als `<pre>`, `application/json` über den
  Tree-Renderer — und **`text/html`-Outputs IMMER in einem eigenen voll
  gesandboxten iframe** (`sandbox=""`, nie ins Panel-DOM — XSS-Fläche).
  Parse-Fehler → Fallback auf Code-Ansicht. Jede Version = prüfbarer Stand
  (Versions-Auswahl im Panel). Die AUSFÜHRUNG von Zellen kommt erst mit
  Phase A (Kernel) — in C schreibt der Agent das Notebook-JSON selbst
  (python_exec, stdlib json; Outputs eingebettet).
- **Ingest**: `doc_convert._extract_ipynb` (stdlib-json, KEIN nbformat) —
  markdown-Zellen verbatim, Code als ```-Fences, Outputs nur text/plain;
  `.ipynb` in `SUPPORTED_EXTS` + `_EXTRACTORS`, bewusst NICHT in
  `_MARKITDOWN_EXTS`. Damit laufen Notebooks durch Projekt-Mining
  (Companion-`.md`), PII-Scan und Klassifizierung (ein Dispatcher, vier
  Konsumenten).

**db_query — Warehouse-Konnektor (v9.356.0; Datenquellen v2 9.368–9.375):**
- **`db_query(source, sql, out?, preview?)`** (documents-Gruppe,
  `engine/tools/data_tools.py`): EIN SQL-Statement gegen eine vom Admin
  **konfigurierte externe Datenbank** (`config.json → data_sources`,
  gitignored/per-Maschine; per Admin-GUI editierbar — Einstellungen →
  Datenquellen). Verdrahtete Typen: **postgres**, **mssql** (v9.368.0:
  pyodbc + „ODBC Driver 17 for SQL Server" — der bank-verifizierte Stack;
  DSN bleibt EIN URL-Feld `mssql://user:pass@host:1433/db`, Brain baut den
  ODBC-String selbst: `SERVER=host,port` mit KOMMA, bewusst OHNE
  Encrypt-Parameter — NICHT auf Driver 18 wechseln; `options.odbc_driver` +
  `options.windows_auth` für Sonderfälle) und **rest** (→ rest_query).
  **Vier Guard-Achsen, Reihenfolge Policy → Scope → Modus → Tabellen:**
  1. **WER (v9.363.0, unverändert)**: `data_sources_access {enabled, roles,
     teams, users}` — additiv, Admins passieren immer, fehlender Block =
     nur Admins; durchgesetzt IM Tool (`data_access_allowed`), KEINE
     Tool-Listen-Mutation (Warm-Pool-KV-Prefix bleibt byte-stabil).
  2. **WAS/WO (v9.370–372, `RequestContext.data_source_scope`)**: pro Turn
     `{quelle: [tabellen]}` aus der **Projekt-Config** (`project.json →
     data_sources`; Projekt-Chats — die Session-Auswahl wird dort ignoriert)
     bzw. der **Session-Auswahl** (Right-Panel-Tab „Datenquellen",
     `sessions.data_sources`; projektlose Chats). **Kein Scope = deny**
     (kein stilles Global-Fallback; `__system__` behält Vollzugriff).
     Tabellen-Whitelist HART via sqlglot (CTE-Namen zählen nicht,
     `information_schema`/mssql-`sys` immer lesbar, unparsebares SQL
     fail-closed). Bei REST-Quellen sind die Scope-Ressourcen
     **Pfad-Präfixe** statt Tabellen.
  3. **Modus (v9.369.0, `access_mode: ro|rw` pro Quelle, Default ro)**:
     ro = nur SELECT/WITH (`_check_statement_allowed`; Write-Versuch nennt
     den Modus, final); rw = +INSERT/UPDATE/DELETE/MERGE mit `conn.commit()`
     und `mode:'rw'`+rowcount im Ergebnis — **DDL bleibt IMMER geblockt**.
     Read-only-Schichten bei ro: Statement-Gate + Session-Read-only
     (postgres `set_session(readonly=True)`) + Read-only-Grant des DB-Users
     (Betriebsvoraussetzung). **MSSQL ehrlich ZWEI-schichtig**: kein
     Session-Read-only — dort tragen Statement-Gate + db_datareader-Login
     (das Tool-Ergebnis sagt das explizit).
  4. **Kontext-Preview (v9.375.0, `context_preview: none|head|full` pro
     Quelle, Default head = 50 Zeilen)**: `none` liefert NUR
     `{columns, row_count}` — keine einzige Rohzeile erreicht den Kontext
     (Datensparsamkeit by design; auch information_schema-Ergebnisse);
     `full` bis 1000 Zeilen. Tool-Parameter `preview` kann den
     Quellen-Default nur VERSCHÄRFEN, nie lockern.
  Serverseitiger **Statement-Timeout** (Default 60 s); Ergebnis-Kappe 200k
  Zeilen; `out='name.parquet'` (bevorzugt — Folgeanalyse via data_query)
  oder `.csv` als Artefakt; GDPR-Pass auf dem Preview (bei `none` gibt es
  nichts zu anonymisieren). **Datensparsame Kette** (Tool-Prosa steert):
  `db_query(out='x.parquet')` → `data_query`-Aggregate/Joins →
  xlsx/Charts — Massendaten fließen nur server-seitig. **Kein
  db_list_sources** (bewusst): falscher `source`-Name listet die Namen im
  Fehler. Credentials: `dsn`/`secret` redigiert der config-Scrubber.
  **Quellen-Steckbrief (v9.374.0, `guide {md, skill?, auto_generated_at?}`
  pro Quelle)**: admin-kuratiertes Nutzungswissen (Tabellen-/Feld-Semantik,
  Join-Pfade, Persistier-Muster; REST: Endpoints) wird **wire-only** in den
  Turn injiziert, wenn die Quelle im Scope ist — Details 05-internals.

**rest_query — REST-Konnektor (v9.373.0, E10):**
- **`rest_query(source, path, method?, params?, body?, out?, preview?)`**
  (documents-Gruppe): erreicht AUSSCHLIESSLICH Pfade unter der
  admin-konfigurierten `base_url` — absolute URLs, `//`, `..` (auch
  percent-encoded) werden abgelehnt, `follow_redirects=False` → SSRF
  strukturell ausgeschlossen; die Quelle ist ein DATENPUNKT, kein Browser
  (harte Abgrenzung zu web_fetch). Gleiche WER/WAS-Achsen wie db_query
  (Scope-Ressourcen = Pfad-Präfixe; `allowed_paths` der Quelle wirken als
  Config-Ebene zusätzlich). `access_mode`: ro = GET/HEAD, rw =
  +POST/PUT/PATCH/DELETE (JSON-Body). Ergebnis: JSON pretty + gekappt
  (`options.max_response_kb`, Default 256), `out='name.json|csv'`
  (CSV-Flatten für Arrays), 4xx/5xx als ERGEBNIS mit Body-Auszug (lesen
  statt blind retryen), `context_preview: none` → nur Status + Bytes +
  Content-Type (Fehler-Bodies bleiben sichtbar). Auth: `auth {kind:
  none|bearer|header|basic, secret|env_key, header_name?}` — das Secret
  verlässt den Server nie. Pagination NICHT automatisch (O5).

## OCR — deterministic local scan toolset (group `ocr`, v9.293.1)

Read text out of SCANNED IMAGES / PHOTOS / scanned PDFs **deterministically** —
local `tesseract` (5.x via pytesseract), **NO LLM, no cloud**. This is the
counterpart to the xlsx toolset for pixels: instead of the model "looking at" an
attached scan and re-typing numbers (it misreads amounts), the server OCRs and
hands back text-faithful output + per-word confidence. Distinct from the PDF
extraction fallback's `local_vision`/`mistral_ocr` (those DO use an LLM/cloud) —
the OCR TOOLS never call a model. Handles images (`.png/.jpg/.tif/.bmp/.webp/
.gif`) and `.pdf` (pages rasterised at 300 dpi via PyMuPDF). Default `lang=
'deu+eng'`. **For digital PDFs with real selectable text use `read_document`;
OCR is for scans/photos.**

- `ocr_inspect(path, lang?, pages?)` — profile WITHOUT full OCR: page count,
  pixel size, orientation/script (tesseract OSD), rough word-count/confidence.
  Call FIRST to pick the language and confirm OCR is worthwhile.
- `ocr_extract(path, mode?, lang?, pages?, out?, model_fallback?)` — full text;
  `mode='text'|'layout'|'markdown'`; preview capped, `out='text.txt'` saves the
  full extract as an artifact. Returns `mean_confidence`.
  **Also returns `model_read`** — the same image as read by the vision OCR
  model, in a SEPARATE field. `text` is deterministic (tesseract, machine-read
  off the pixels); `model_read` is UNVERIFIED and **can invent** plausible
  names/numbers on unreadable images. Rationale: on photographed documents
  tesseract silently under-reads (measured: passport number read 1/10 vs 5/10
  for the model) and does not know it failed — so both are offered and never
  merged. `text` = evidence, `model_read` = lead; never quote a value from
  `model_read` as fact. `model_fallback=false` for a strictly deterministic
  result.
- `ocr_region(path, bbox=[x,y,w,h], unit?, page?, lang?, model_fallback?)` — OCR
  only a rectangle ('just the stamp', 'only the footer'); `unit='px'` (default)
  or `'pct'`. Also returns `model_read`: the crop is already a picture, so the
  vision OCR model reads the SAME crop as a flagged second opinion. Without it,
  the tool meant for the HARD cases ('read just the handwritten number') was
  stuck with the weaker reader.
- `ocr_fields(path, fields=[{name,pattern}], lang?, pages?, model_fallback?)` —
  STRUCTURED extraction: OCR then apply your per-field REGEX (one capture group
  = the value). Returns validated JSON `{name: value|null}` + `unmatched` + a
  **`source`** map. For invoices/receipts/forms. Bad regex is reported, never
  raised.
  Where the deterministic OCR finds nothing for a field, the pattern is retried
  against the vision model's reading and `source[field]` becomes
  `model_unverified` (vs `ocr` = read off the pixels). Measured on a real
  passport photo: **all four fields came back `null` before**, all four correct
  now — but a `model_unverified` value **can be invented**, so verify it
  (`ocr_region`) before quoting it as fact. `model_fallback=false` = strictly
  deterministic.
- `ocr_tables(path, out?, lang?, pages?)` — geometric column/row clustering of
  OCR words → CSV; `out='table.csv'` saves the full table, which a follow-up
  `xlsx_inspect`/`xlsx_query` can then read (OCR→spreadsheet pipeline).

Page counts are logged to the cost ledger (`purpose='ocr'`, $0 — local engine —
but the page count is an audit signal, same as the cloud OCR path). Needs the
`tesseract` binary + language data on the host (`brew install tesseract
tesseract-lang`; on the Windows bundle the Tesseract installer ships in
`installers\` — run it once, choosing the deu+eng language data); every tool
fails LOUD with the install hint if it's missing.

## Document verification — deterministic checks (group `doc_checks`, v9.335.0)

The KYC/fraud-analysis toolset (L1 of the PII-parity plan): the checks a
document analysis stands on — MRZ check digits, date relations, identity
consistency across documents — run SERVER-SIDE ON THE RAW FILES and return
PII-FREE VERDICTS. They work identically whether the GDPR anonymiser is on or
off (the model never needs the protected values to reason about the results),
and they are IMMUNE to pseudonymised values: the model must NOT do this
arithmetic itself — on anonymised (day-jittered/tokenised) values its own math
produces FALSE forgery indications.

- `mrz_verify(path?, text?, lang?)` — parses the machine-readable zone
  (TD1/TD2/TD3) and verifies all ICAO-9303 check digits (weights 7,3,1).
  Prefers `path`; a dedicated whitelist OCR pass (`A-Z0-9<`, bottom-strip +
  full-frame crops) reads MRZs that the generic OCR garbles on phone photos.
  Verdicts only: per-field checksum true/false/null, `all_valid` (needs ≥3
  checkable digits, else `partial: true`), doc type, issuer, nationality,
  expiry state vs today + expiry month, age — NEVER the number, name or raw
  birth date. Unreadable fields are `null`, never `false` (an OCR garble must
  not read as a forgery indication).
- `doc_dates_check(sources, pairs?, lang?)` — date RELATIONS instead of model
  arithmetic: each source is `{name, date}` (literal, all common formats incl.
  EXIF `2026:07:02` and textual months `5 FEB 1947`) or `{name, path, select}`
  (`exif_datetime`/`mrz_dob`/`mrz_expiry`/`file_mtime`). Returns per-source
  past/future vs today and CALENDAR-exact pairwise gaps (`'10y - 1d'`,
  `'9 days'` — leap-year-safe). Birth-named sources return age only.
- `identity_consistency(paths, lang?)` — 'are the personal details identical
  across these documents?': extracts each file's MRZ identity + filename
  name-form, clusters names across case/order/initials/MRZ-form with
  conservative OCR-garble fuzzy matching (`engine/identity.py`), and reports
  distinct-person count, DOB equality (+ age), distinct document numbers and
  the expiry chain. Deviating surface forms are listed per source as
  discrepancies (that text passes the GDPR tool-result seam).

Measured on the real reference set (10 JPGs of chat 58e3c521438a): the
high-quality scan verifies fully (`all_valid: true`), a low-res photo yields an
honest partial (number checksum verified, dates `null`), video-legitimation
screenshots with the MRZ out of frame return `mrz_found: false` — and the
identity comparison clusters scan + photo + filename to ONE person with
matching DOB.

## Memory (MemPalace, direct — not MCP)

- `mempalace_query(query, wing?, room?, limit?)` — semantic search.
  In a project chat, force-scoped to `project__<id>`.
  - File-backed drawers return the matched chunk widened to its neighbours
    (prev+match+next, ~2–2.5 KB) inline + `content_via:"snippet+optional_read"`.
    `read_document(read_path)` when EITHER you need an exact quote/figure/table,
    OR the answer isn't fully in the window (cut off / detail continues beyond
    it); if the window answers the question, answer from it — don't read just to
    be thorough. Drawers with no file behind them (chat/profile/artifact) return
    their full verbatim text inline + `content_via:"snippet"`. (History:
    v9.34.0 BLANKED the snippet to force reads; v9.37.0 brought it back, widened
    + read-optional, to cut token cost — same trade-off as the KG span above.)
  - **Matched-regions auto-read** (v9.39.0): mempalace_query records which
    chunk_indices of each file matched this session; a follow-up
    `read_document(read_path)` on that `.md` returns ONLY the matched regions
    (union of ±2-chunk windows around each matched chunk), `format:"text-regions"`,
    not the whole file — files often match on scattered chunks, so this gets
    every relevant region at a fraction of the bytes. Automatic (no flag). Falls
    back to a full read when offset/limit is given, the file wasn't a query hit,
    or the regions cover ~the whole file. Eval: read bytes -71% (461->130 KB) at
    a measured -0.07 mean quality cost (occasionally clips needed context).
    Smart gates (v9.40.0): returns the WHOLE file (no trim) when the file is
    small (≤8 chunks / ≤6 KB) OR when the matched regions would add up to ≥75%
    of the file anyway (many scattered small matches negate the saving) —
    trims only when a large file has genuinely sparse matches.
  - **Cross-encoder reranker** (v9.38.0, `config.json → mempalace.reranker`,
    default ON): after vector retrieval, a BAAI/bge-reranker-v2-m3 cross-encoder
    re-ranks the top `top_k_in` (40) candidates by joint (query,passage) scoring;
    `matched_via` gains `+rerank`. Skipped when the top hit has a strong filename
    boost (≥0.20). Eval lifted wrong-doc-choice cases (C3/P2/C2) but slightly hurt
    out-of-corpus refusal (surfaces plausible-but-irrelevant passages).
- `mempalace_kg_query(...)` — entity/predicate filter on the KG
- `mempalace_kg_search(query)` — semantic KG search
- `mempalace_kg_neighbors(entity, depth?)` — entity neighborhood
  - All three KG tools return triples (subject/predicate/object + source_file
    + confidence) **plus a short verbatim `span`** (≤400 chars, capped) quoting
    the source when available. The span quotes a short fact directly;
    `read_document(source_file)` when you need surrounding context / an exact
    figure / text beyond the span, OR when the span doesn't itself contain what
    the question asks. (History: v9.36.0 STRIPPED the span
    to force reads after the eval P2/C2 wrong-document failures; v9.37.0 brought
    it back — capped + read-optional — because forcing a full read on every hit
    blew up token cost. Trade-off: span reopens some mis-cite risk, mitigated by
    the cap + a hint warning not to answer from a span that doesn't support the
    claim.)
- `save_chat_to_memory()` — flip current chat's `save_to_memory` to ON

(`mempalace_get_drawer`, `mempalace_list_drawers` are admin-side; see
`03-storage.md` for direct SQLite if you need to inspect MemPalace.)

### Wiki tools (the agent's long-term memory = the user-visible LLM Wiki)

As of v9.103.0 the wiki IS the agent's memory: a user-visible, editable page
tree, every saved page mirrored into MemPalace for search. These REPLACED the
old `memory_store`/`memory_recall`/`memory_delete`/`memory_shared` tools (gone).
In the `wiki` group. Scope `user` (private) | `team` (shared with the team) |
`global` (everyone). Access is enforced; pages nest via `parent_id`.

- `wiki_write(title, content?, page_id?, scope?, parent_id?, project?)` — create
  a page (give `title`) or update one (give `page_id`). Write durable facts/
  notes/summaries here. A human/agent edit makes a new version; only the current
  version is searchable.
- `wiki_read(query?, page_id?, filter?, limit?)` — `page_id` reads one full page;
  `query` searches the wiki semantically across ALL accessible wings (user +
  teams + global); neither lists the tree (`filter`: mine|team|global|all).
- `wiki_delete(page_id)` — delete a page (children re-parent to its parent).
- `wiki_structure(action?, filter?, page_id?, parent_id?, position?)` — `list`
  the accessible tree (default) or `move` a page (re-parent/reposition).

See `01-api.md` (LLM Wiki endpoints) + `03-storage.md` (wiki_pages schema). The
old MemoryStore .md-file backend is retired; the per-page history, promote, and
auto-feed-from-chat behavior live in the wiki, not a key/value store.

## Context manager

- `context_search(query)` — search the LCM DAG
- `context_detail(node_id)` — one node's content + lineage
- `context_recall(query)` — natural-language recall

## Web / email

- `web_fetch(url)` — GET one URL, returns its FULL content (the whole page;
  there is no summary/abstract mode — a page is always read in full) tagged
  with a `fetch_method`: `raw` (non-HTML, or HTML nothing converted) /
  `markitdown` (our HTML→markdown) / `crawl4ai` (headless-browser render) /
  `document` (the URL was a file — PDF/DOCX/XLSX/PPTX/CSV — extracted via
  doc_convert) / `image` (the URL was an image, described by a vision model) /
  `academic` (academic landing page resolved to its full-text PDF).
  markitdown is tried first; the crawl4ai headless render fires **only**
  when the converted text is near-empty (<30 chars) on an HTML GET — so
  JS-rendered pages get rendered, static pages never pay the browser cost.
  A URL that resolves to a FILE rather than a web page (a direct `…/foo.pdf`
  link, a `.docx`/`.xlsx`/`.pptx`/`.csv`, or an image) is ingested like an
  uploaded file — its text is extracted (or the image described) instead of
  the raw bytes being returned. Academic landing pages (arxiv,
  bioRxiv/medRxiv, PubMed Central) are auto-resolved to their full-text PDF —
  just pass the abstract URL.
  **Audio + YouTube (v9.307.0)**: a direct audio URL (`.mp3`/`.m4a`/`.wav`/…
  or an `audio/*` Content-Type) is TRANSCRIBED via the shared STT pipeline
  (default: local Whisper, $0) → `fetch_method: audio-transcript`; a YouTube
  link (watch/shorts/live/youtu.be) is downloaded as audio via yt-dlp (host
  dependency; bounded 80 MB / 300 s) and transcribed → `youtube-transcript`.
  Works everywhere web_fetch is used: chat, Websuche-basket prefetch, AND the
  project web_urls miner (a linked video becomes project knowledge).
  The chat view shows the method as a colored badge.
- `exa_search(query, num_results?)` — semantic web search (Exa cloud, API
  key). **Search-only**: returns title + link, no page content. After a
  search, `web_fetch` the most relevant URLs (up to 5, in parallel) and answer
  from the full page text — never from titles/URLs alone.
- `searxng_search(query, num_results?)` — self-hosted SearXNG
  search (no API key). Returns a ranked list of `title` + `link` + `score`
  ONLY — **no snippets** to the model (v9.99.2: snippets were biasing the
  model's fetch choice toward whoever had a tempting blurb instead of the
  source that best answers the intent). The model must then `web_fetch` the
  top URLs (up to 5, in parallel) and answer from page text — never from
  titles — preferring primary/authoritative pages over outlets that merely
  mention the topic. An `infobox` is still surfaced when available. Always
  searches the broad `general` category (v9.124.0: the `news` category param
  was dropped — `general` already returns news outlets AND the authoritative
  source pages, while `news` buried the authoritative page and added noise on
  non-news queries).
  The human Websuche curation panel still shows ~300-char snippets (server
  passes `include_snippets=True` on that path). This is a **standalone
  tool**, not an exa_search backend. Default-disabled at the global gate —
  admin enables it in Settings → Tools.
- **Specialized searches** (v9.288.0) — same self-hosted SearXNG instance +
  same `title/link/score` result shape as `searxng_search`, but each scoped to
  a topic CATEGORY so the model can deliberately target the right sources. All
  route through the shared `_searxng_query` core; all default-active; all still
  require a follow-up `web_fetch` to read the pages. Deliberately SEPARATE tools
  (not a `category` param) so the model opts into each — this is why the old
  ad-hoc `news` category footgun (v9.124.0) can't recur:
  - `science_search(query, num_results?)` — `science` category: arxiv, PubMed,
    Google Scholar, Semantic Scholar. Papers/studies, often with publication
    dates. For academic/medical/scientific literature.
  - `dev_search(query, num_results?)` — `it` category: Stack Overflow, MDN,
    GitHub, Ask Ubuntu, PyPI, Docker Hub. Programming Q&A + docs. DISTINCT from
    `code_search` (which queries this codebase's own code-structure graph).
  - `image_search(query, num_results?)` — `images` category: Google/Bing/Qwant/
    Brave Images, Flickr, Openverse. Each result carries an `image_url` (the
    DIRECT picture URL) beside the source `link`. To describe a picture,
    `web_fetch` its `image_url`.
  - `news_search(query, num_results?)` — `news` category: Google/Bing/DDG/Qwant
    News, Reuters. Dated news items. Use ONLY when the user wants press coverage/
    recent events; for facts/live data prefer `searxng_search` (general).
  The Web-Search settings panel (Settings → Server → Websuche) shows each of
  these tools with an on/off toggle and the health of the engines backing it,
  re-probed every 4 hours.
- `email_accounts` / `email_inbox` / `email_read(id)` / `email_search(q)` /
  `email_send` / `email_reply` — provider-agnostische E-Mail-Tools (v9.365.0,
  vormals `gmail_*`) über konfigurierbare Konnektor-Konten
  (`tools_config.json → email.accounts[]`, Typen `imap` / `pop3` /
  `exchange_ews`). Mehrere Konten parallel; jedes Tool nimmt optional
  `account` (leer = Standard-Konto), `email_accounts` listet Namen, Typ,
  Adresse und Capabilities (Ordner, Server-Suche, Reply) je Konto — ein
  unbekannter Kontoname nennt die verfügbaren Namen im Fehler. Gmail ist ein
  Preset des IMAP-Konnektors (App-Passwort; Suche nutzt dort weiterhin die
  volle Gmail-Syntax via X-GM-RAW). Sonst gilt die einfache Such-Syntax
  `from:` / `subject:` / `to:` + Freitext; POP3 hat keine Server-Suche und
  filtert clientseitig über die letzten Nachrichten (steht im Ergebnis als
  `search_scope`). Exchange = On-Prem EWS via `exchangelib`
  (Benutzername/Passwort, kein OAuth/Graph). `email_send` prüft jede
  Empfängeradresse deterministisch auf RFC-Form (Pseudonym-Tokens scheitern
  damit IMMER, nicht nur zufällig).
- **In anonymisierenden Sitzungen (v9.343.0)**: `email_send`/`email_reply` sind
  **Egress-Tools** und laufen durch dasselbe Gate wie die Web-Tools — enthält ein
  Argument einen geschützten Wert oder ein Pseudonym, wird der Versand
  **verweigert** (`web_query_blocked_pii`). Grund: ein Fake-Empfänger sieht wie
  eine echte Adresse aus, die Mail ginge an einen fremden Dritten. **Anhänge sind
  bei aktivem Mapping komplett gesperrt** (die Artefakt-Datei auf der Platte ist
  bereits rückübersetzt → Fake-Text + Klartext-Anhang). Die Lese-Tools
  (`email_inbox`/`read`/`search`, auch `email_accounts`) pseudonymisieren ihr
  Ergebnis (fremde Mail-Inhalte sind fremde personenbezogene Daten).
- **Firmen-Recherche trotz Anonymisierung (v9.344.0, Auto-Release)**: Suchst du
  in einer anonymisierenden Sitzung mit einem **Firmen**-Pseudonym, wird der
  Call NICHT mehr verweigert — das Gate setzt für die ausgehende Anfrage
  automatisch den echten Firmennamen ein (die Kategorie `business_id` lässt die
  Policy ohnehin passieren; du selbst bekommst den echten Namen nie zu sehen,
  und die Ergebnisse kommen pseudonymisiert zurück). Adverse-Media-, Sanktions-
  und Registerabgleiche laufen damit vollständig. **Bei PERSONEN-Pseudonymen
  bleibt es bei der Verweigerung** — in JEDEM Modus: Fake-Namen sind reale
  Namen, eine solche Suche träfe eine echte fremde Person und würde die Analyse
  mit deren Daten vergiften. Enthält eine Query BEIDES, kippt die Person den
  ganzen Call. Weise die Prüfung dann als „nicht prüfbar (Datenschutz)" aus
  oder frage nach einer Freigabe.

## Code execution

- `python_exec(code, timeout?)` — subprocess (`sys.executable`).
  Working dir = session's artifact folder. Each call is a FRESH process —
  no in-memory state carries over (files on disk persist; for in-memory
  state across calls see `kernel_exec` below). Files written
  auto-register as artifacts.
  **In a code-mode project** (v9.312.12) the cwd is this chat's — or this
  sub-agent's — output folder (`chats/<title>_<date>_<id>/[subagents/<task>/]`),
  NOT the project root. So a plain relative write (`open('report.html','w')`)
  lands in the output folder by construction, and generated files can no longer
  fall into the user's source tree. Relative *reads* of the source
  (`open('q1/x.sql')`, `Path('q1').rglob(…)`, `glob`, `os.walk`) still work: the
  project's top-level entries are symlinked into the folder for the duration of
  the run and removed afterwards. `$BRAIN_OUT` (output folder) and `$BRAIN_ROOT`
  (project root) hold the absolute paths.
  **`execute_command` is different**: it keeps cwd = project root (its commands
  are mostly reads across the source tree), so it must write via `$BRAIN_OUT`.
  **Quant-Pakete (v9.354.0)**: über den konfigurierten `venv_path`
  (`tools_config.json → python_exec`) stehen zusätzlich matplotlib, seaborn,
  statsmodels, arch und QuantLib bereit (Plots, VaR/GARCH, Regressionen);
  duckdb/pyarrow/openpyxl sind global installiert. Default-Timeout ist 120 s.
- `r_exec(code, timeout?)` — R-Skripte via `Rscript` (v9.354.0), Spiegel von
  `python_exec`: gleiche cwd-Logik (Artefakt-Ordner), gleicher Ordner-Diff
  (geschriebene Dateien — `write.csv`, `png()` — registrieren sich als
  Artefakte), gleicher GDPR-Pass auf stdout, per-Tool-Kill. Für bestehenden
  R-Code und R-spezifische Statistik; für Python weiterhin `python_exec`.
  Default-Timeout 120 s. Pakete kommen aus der System-R-Library (kein venv).
- **Persistente Kernel (v9.359.0, Quant-Workbench Phase A)** — Zustand
  ÜBERLEBT zwischen Tool-Calls und Turns (Jupyter: ipykernel/IRkernel als
  eigene Subprozesse, EIN Kernel pro Chat-Session, max. 3 gleichzeitig,
  Idle-Abbau nach ~20 min, stirbt mit Brain-Restart):
  - `kernel_exec(code, lang='python'|'r', timeout?)` — startet den
    Session-Kernel lazy beim ersten Call. Großen Datensatz EINMAL laden,
    dann über Folgefragen iterieren (kein Neuladen). cwd = Artefakt-Ordner;
    geschriebene Dateien + `plt.show()`/R-`plot()`-PNGs (als
    `kernel_plot_N.png`) registrieren sich als Artefakte mit Provenance
    `produced_by='kernel#N'` + Env-Snapshot. stdout läuft durch den
    GDPR-Pass. Timeout → Interrupt (Kernel + Variablen überleben);
    Cancel-Eskalation: 1. Abbruch = Interrupt, 2. = Kernel-Kill.
    NUR im interaktiven Chat (Scheduler/Background → `python_exec`/`r_exec`).
  - `kernel_status()` — Sprache, Uptime, RSS, Exec-Zähler, definierte
    Top-Level-Namen (erst prüfen, dann ggf. neu laden).
  - `kernel_restart(lang?)` — expliziter Neustart bzw. Sprachwechsel
    (python↔r); ALLE Variablen gehen verloren.
  UI: Statusleisten-Badge „Kernel · py/R · RSS" mit Neustart-Knopf;
  Endpoints `GET /v1/kernel/status?session_id=` + `POST /v1/kernel/restart`.

## Delegation / workers

- `delegate_task(agent, prompt, model?, wait?)` — delegate to ANOTHER agent.
  With `wait=true` it blocks up to 300s; if the delegate is still working then,
  the tool returns `status: "running"` (NOT an error) — poll `task_status`.
  Errors come back as tool errors in the SAME turn, so the calling model can
  retry, pick a different `model`, or do the work itself. `task_cancel` now
  actually stops the delegate's in-flight LLM call (not just the status row).
- `task_status(task_id)` / `task_cancel(task_id)`
- `get_artifact_detail(id)` — artifact metadata
- The `worker_*` control tools (worker_status/abort/pause/resume/send/ask_user)
  were REMOVED 2026-07-13 — their registry had no writer since the native loop
  was deleted, so they could never affect anything.

## Background tasks (group `background`)

- `run_background_task(title, prompt, group_id?, follow_up?)` — spin off a long,
  output-heavy run as a DETACHED background task (same agent, same model/tools as
  the chat). Returns immediately with a `task_id`; the spawning turn ends — it
  does NOT block. When it finishes, the server **auto-delivers** the result into
  the chat (an auto-fired turn if the chat is idle; otherwise it rides the next
  user turn), so just acknowledge it's started and stop. Differs from
  `delegate_task` (which targets ANOTHER agent and can wait for the result). The
  user sees/controls it in the "Hintergrundaufgaben" panel (live progress, Stopp,
  Transkript). Use only for genuinely long work; quick lookups stay inline.
  **Fan-out (parallel):** for a request with several INDEPENDENT subjects, make
  one call per subject sharing the SAME `group_id`, and put the recombine step
  (compare/summarise/recommend) in `follow_up`. The parts run concurrently and
  the whole group is delivered back in ONE join turn that carries out `follow_up`
  — do NOT create a separate summary task. Calls made in the same turn are
  grouped automatically even without an explicit `group_id`. A background task
  may NOT itself start background tasks (no nesting).
- `retry_background_task(task_id, model?)` — retry ONE failed background task
  (status `error`/`timeout`/`empty`), allowed EXACTLY ONCE per task —
  server-enforced via the `retry_of` column (a retry can't be retried, and a
  task with an existing retry is refused). `model` reruns on a different
  (enabled) model — for model-related failures (refusal, empty answer,
  provider errors). USER-cancelled tasks are refused: a deliberate Stopp is
  the user's decision. The retry joins as its own group; the delivery turn
  re-attaches the ORIGINAL group's successful sibling outputs from the DB so
  the combine step sees the full set again.
- **Failure semantics (2026-07-13 hardening):** every task ends in one of
  `done | cancelled | error | timeout | empty`. `timeout` = the enforced 1h
  wall-clock limit fired (partial kept); `empty` = finished without error but
  produced no output. The delivery preamble labels each failed member with its
  class + `task_id` and instructs the model: error/timeout/empty → retry once
  / do it inline / report; user-cancel → never restart unasked, use the
  partial, ask the user if the result is essential.

## Scheduler (admin-side from chat)

- `schedule_list()` — every visible schedule (read-only)
- `schedule_history(name?, limit?)` — past runs

(For create/edit/delete from a chat, hit the HTTP API — see `01-api.md`
"Scheduler" + `04-recipes.md`.)

## Code intelligence (codebase-memory)

As of v9.214.0 the code-graph subsystem is powered by the **codebase-memory-mcp**
engine (a brain-managed binary, run per call as a CLI subprocess — not MCP),
replacing the old in-tree tree-sitter CodeGraph. Four tools:

- `code_search(query | name_pattern | semantic_query)` — FIND code: BM25
  natural-language (`query`), regex on names (`name_pattern`), or embedding
  search (`semantic_query` = array of keywords). The discovery workhorse.
- `code_trace(function_name, direction)` — callers (`inbound`) / callees
  (`outbound`) of a function.
- `code_query(cypher)` — read-only Cypher for complex/multi-hop structural
  questions.
- `code_snippet(qualified_name)` — read the source of a symbol.
- `ast_grep_search(pattern, root?, lang?, max_results?)` — **structural (AST)
  search** (9.310.0): find code by SYNTAX pattern, not text — `str($A)` = every
  one-arg str() call, `$$$` = any number of nodes. Backed by the `ast-grep`
  host binary (brew install ast-grep; missing → clear error). Root defaults to
  the code-mode working dir. Read-only (plan-mode allowed).
- `ast_grep_replace(pattern, rewrite, root?, lang?, apply?)` — **structural
  refactor** (9.310.0): rewrite every match; rewrite may reuse the pattern's
  metavariables (`str($A)` → `repr($A)`). SAFE BY DEFAULT: without `apply=true`
  it's a dry-run preview. >500 matches refused; changed files feed the code
  index via `_after_file_write`.

Per-tenant: each **code-mode project** gets its own index under its project dir
(`.cbm-cache`, indexed from its working directory); the shared brain-source
index (what Brainy queries) stays separate. In a normal chat these tools are
deferred (discover via `tool_search`); in a code-mode project they're active.
The index is built when BRAIN.md is written and kept fresh by a daemon that
re-indexes on file changes. In the code-mode project view, operators get
Refresh / Clean&rebuild / Graph-view / History buttons plus a per-file index
state (indexed / stale / not-indexed). Querying an unbuilt index returns a
"build it first" hint rather than empty results.

## Git / GitHub

- `git_command(cmd, cwd?)` — subset of git verbs
- `github_command(...)` — `gh` CLI passthrough
- `git_worktree(action, slug?, base?, purpose?, force?)` — **worktree lanes**
  (9.311.0, code-mode only): isolated checkouts under
  `<working_dir>/.worktrees/<slug>` on branch `brain/<slug>` for risky/parallel
  work. Actions `create` / `list` / `diff` (base...branch + uncommitted) /
  `remove` (refuses a dirty lane without `force`). NO auto-merge — integration
  is the user's deliberate terminal action; the tool prose instructs the agent
  to confirm create/remove with the user first. Lanes are git-ignored via
  `.git/info/exclude` (repo-private, never the user's .gitignore); registry
  (purpose/base/created) in `.worktrees/lanes.json`.

## MCP

- `mcp_servers()` — list connected MCP servers
- `mcp_connect(spec)` / `mcp_disconnect(name)` — manage

## Skills

- `use_skill(skill="<slug>")` — load full SKILL.md body into context.
  This is how you load THIS skill; load others the same way. Resolves in this
  order: built-in agent skills (`skills/` + main's global), then the caller's
  visible **per-user skills** (`user_skills/`, own + shared) — the latter are
  access-gated by the same sharing block as chats, so a user only loads a
  per-user skill they own or that was shared with them (v9.294.0).
- `find_skills(task="<description>")` — search the current user's PERSONAL
  skills (own + shared) for ones matching a task (v9.294.0). Built-in skills are
  listed in the system prompt, but per-user skills are NOT (that would break the
  cached KV prefix) — they are discovered via this tool instead. Returns
  `[{slug, name, description, score, matched_via}]` (ACL-filtered; empty list =
  no match, just proceed). Then `use_skill(skill="<slug>")` loads the winner.
  This is the discovery half of "personal skills as tools": the tool DEFINITION
  is static (cache-safe), the per-user matches ride in the tool RESULT.
  Matching merges two signals: **semantic** (MemPalace/MLX vector search — so a
  paraphrased or cross-language task like "check a passport" finds a German
  "Ausweisprüfung" skill; `matched_via:"semantic"`, score = similarity 0–1) and
  **keyword** overlap (`matched_via:"keyword"`). Both cover the full visible set
  (own + team/global-shared): the semantic pass (v9.294.2) queries across the
  owner wings of every visible skill — a shared skill's drawer lives in its
  OWNER's `user__<owner>` wing — and drops any hit whose (owner, slug) isn't in
  the caller's visible set (no leak). Falls back to keyword-only if the vector
  store is unavailable. `GET /v1/skills/match?task=` exposes this ranking to the
  UI (used by the workflow-generate modal to offer a skill to reference).

## Discovery

- `tool_search(query)` — find deferred tools. Returns name + schema for
  matching tools so the LLM can invoke them in the next round.

## Helpdesk (Brainy-only)

Only available when `purpose='helpdesk'` (the Brainy bubble). Not in normal
chat. No args — they read scope from the request context (current user +
session).

- `helpdesk_session_info()` — facts about the chat session Brainy was
  opened from (model, project, message count, …).
- `helpdesk_user_context()` — the caller's profile / preferences.
- `helpdesk_user_activity()` — the caller's recent chats / schedules /
  usage, **plus their code-mode terminal chats** (`terminal_chats[]`, each
  with a `live` flag; `terminal_chats_live_now` counts those with a turn
  streaming right now — `code_chat` sessions are excluded from the normal
  chat list, so this is how Brainy sees whether a terminal chat is active).
- `helpdesk_config({section, enabled_only?})` (9.314.0) — the LIVE settings
  relevant to a question, as facts-JSON for Brainy to analyse. Sections:
  `models` (per model: $/M prices, capability 0-100 **per task type**
  [coding/math/research/analysis/reporting/creative/orchestration/agentic/fast],
  measured tps, `is_local`, context window, billing account + `billed_at_zero`),
  `coding_plans`, `quotas` (config **without** `user_overrides` + the caller's
  own usage), `providers` (keys only as a COUNT, never content), `cost_rates`
  (+ the unpriced-models list), `service_models`. Goes through the SAME seams
  as the HTTP endpoints (`resolve_model_plan_id`, `model_is_flat_plan`,
  permission scoping via `get_user_allowed_models`, `_server_config()` live
  mirror) — NOT a raw config.json read; secrets appear in no section. THE tool
  for "which model for X / what does it cost / is it local / what's my limit".
  Careful: `coding_plans` entries are BILLING ACCOUNTS, not models.

Brainy's full fixed read-only set (`_HELPDESK_TOOLS`): `use_skill`, the
four `helpdesk_*` tools, `mempalace_query`, `read_document`, `read_file`,
`list_directory`, `search_files`, `context_search`, `context_detail`,
`context_recall`, `web_fetch`, `exa_search`, `searxng_search`. Every
write/exec tool is deliberately excluded.

## User interaction

- `ask_user(question)` — pause turn, wait for user reply (blocks via
  `/v1/chat/answer`). Works from a detached BACKGROUND task too (v9.312.5):
  there is no live SSE channel there (the spawning turn is long over), so the
  question is keyed on the TASK id (not the session — several sub-agents may
  block at once) and PERSISTED to `background_tasks.pending_question`. The 3s
  running-tasks poller carries it to the UI, which renders an answer box on the
  sub-agent's card; the answer comes back via `POST /v1/chat/answer {task_id}`.
  The row is cleared on every exit (answered / timed out / errored).
- `ask_user_for_file(prompt)` — same, file upload
- `ask_llm(prompt, model?)` — sub-LLM call (workflow building block)
- `agent_step(instruction, plan?, files?, model?, max_rounds?, expected_output?)`
  — v9.290.0, group `workflows`: runs ONE bounded agentic turn as a workflow
  step ("Der Plan ist das Programm"). The .flow script stays the deterministic
  spine; judgment-heavy plan steps run agentically via `background_call` with
  the dedicated purpose `workflow_step` (own tool-matrix column
  "Workflow-Schritt": files/exec/documents/web/KG-query, deliberately WITHOUT
  the workflows group, delegation and ask_user — recursion/blocking guard).
  Shared workspace = the run's `wf-<exec_id>` artifact folder (relative
  writes land there, same folder as .flow-level write_file — later steps
  and the verify step see earlier steps' files). Image inputs in `files=`
  are sent as native image blocks when the model's raw_formats accept the
  MIME (`model_supports_mimes`), so visual plan steps work. Model:
  arg → workflow MODEL header → background default; max_rounds default 16,
  cap 24 (whole-plan steps should set 20); returns
  `{text, model, rounds, files}`. In-flight steps are cancelled with the
  workflow (turn-id registry on the WorkflowExecution). The plan markdown
  lives in the `<name>.plan.md` sidecar and reaches the script as the
  pre-seeded `plan_md` variable; the DSL builtin `plan_steps(md)` splits it
  deterministically into `[{index, title, body}]`.
  **`skill="<slug>"` (v9.294.2)**: instead of an inline plan, a step can run a
  SAVED skill as its method — the skill's body becomes the plan (an explicit
  `plan=` is appended as extra context). Resolves the workflow OWNER's visible
  skills (built-in, then per-user + shared, ACL-gated via the run's user_id).
  So a workflow references a reusable procedure once instead of duplicating a
  plan.md — e.g. the Ausweisprüfung workflow uses
  `agent_step skill="ausweispruefung-echtheit-faelschungsmerkmale"`.

## Translation

- `translate_text(text, target, source?, glossary?)`
- `translate_document(path, target, …)`
- `detect_language(text)`
- `list_glossaries()` / `get_glossary(slug)`
- `transcribe_audio(path)` — Whisper/Voxtral. Routes only to models flagged with the
  `audio_transcription` capability (verbatim speech-to-text): local Whisper
  (tiny/base/small/medium/large-v3 plus large-v3-turbo — turbo ≈ same quality on
  clean audio but much faster; on noisy/phone audio prefer large-v3 or Voxtral) +
  cloud Voxtral-mini. Models with only the plain `audio` capability (audio-in *chat* /
  understanding — voxtral-small, gemma-4, glm) are **excluded** — they can't drive the
  `/audio/transcriptions` endpoint and would 400 at the wire.
- `generate_audio_overview(topic?, audience?, length?)` — NotebookLM-style **audio
  overview / podcast**. Generates a spoken conversation (default two hosts,
  Oliver & Jane; the engine supports 1–4 speakers with personas since v9.304.0)
  voiced via TTS into a `.mp3` (+ a `.md` dialogue script) in the session artifact
  folder. **Source depends on context:** in a PROJECT it discusses the project's
  sources; OUTSIDE a project it discusses the CURRENT CHAT's conversation (so any
  chat can become a podcast). **Multilingual:** the material's language is
  auto-detected and the podcast is spoken in it (Voxtral's 9 languages:
  en/fr/de/es/nl/pt/it/hi/ar), using a voice tagged for that language if one
  exists (else the English default voices — clone a native voice in Settings →
  Tools to upgrade). `length` ∈ short|std|long. (group: `audio`)

## Image / media

- `generate_image(prompt, size?, ...)` — text-to-image for PHOTOS/ILLUSTRATIONS
  only. NOT for diagrams/charts/org charts/flowcharts/timelines — a diffusion
  model can't render legible exact text (labels come out as garbled glyphs). Any
  diagram/chart — even when the user asks for it "as PNG" or "as an image file" —
  is `render_diagram`, NOT generate_image. (The prompt classifier has a dedicated
  `diagram` tool word → the `documents` group, so such requests route to
  render_diagram automatically.)
  **Datenschutz (v9.343.0)**: `generate_image` schickt den Prompt **IMMER** an
  einen Cloud-Dienst (api.mistral.ai) — auch aus einer lokalen Sitzung. Es ist
  daher ein **Egress-Tool** (Gate wie bei den Web-Tools) und hat zusätzlich einen
  **mapping-unabhängigen** PII-Scan: enthält der Prompt personenbezogene Daten,
  wird er NICHT gesendet (`cloud_egress_blocked_pii`) — formuliere ihn dann mit
  Platzhaltern („Person A") statt echter Namen. `render_diagram` läuft dagegen
  **lokal**, bekommt die ECHTEN Werte und ist nicht gegatet — für alles mit
  Personen-/Firmendaten im Bild ist es damit auch der datenschutzrichtige Weg.
- `render_diagram(code, format?, title?, theme?, background?)` — render a Mermaid
  diagram to a real SVG/PNG/PDF **artifact** (via mermaid-cli, exact legible
  text). For org charts/flowcharts/structure/timeline/sequence/ER/gantt/etc.
  Returns `path` + `embed` snippets. **For a chat-only diagram**, just write an
  inline ` ```mermaid ` block (rendered live, no tool). **For a report/
  presentation**: either call `render_diagram` then embed `![title](file.png)`,
  OR — simpler since v9.209.0 — just put the ` ```mermaid ` (or bare ` ```gantt `/
  ` ```flowchart `) block straight into `write_document` content; it is
  auto-rendered to a brand-themed PNG and embedded (docx/pdf/html), falling back to
  the code block on a Mermaid syntax error. Default format is **PNG** (high-DPI, scale 4 / width
  2000) and embeds in PDF, DOCX AND HTML — take the default for reports. SVG is
  available (`format=svg`) but embeds in HTML ONLY (the PDF/DOCX writers cannot
  place an SVG → emit a "render as PNG" placeholder). write_document embeds
  `![](file)` PNG/JPG images as real pictures in docx/pptx/pdf. **Brand styling:**
  diagrams automatically take the doc-style preset's brand colors + font (node
  fills/borders/edges/pie palette derived from `colors.accent`/`colors.heading`,
  font from `fonts.body`) so they match the report — even when no `style=` is
  passed (the default preset resolves like write_document). Pass an explicit
  `theme=` (default/dark/forest/neutral) to use a generic Mermaid theme instead,
  or `style=""` to opt out of brand colors.

## Nodes (distributed compute)

- `list_nodes()` — peer nodes available

## Thinking (scratchpad / "Spickzettel")

- `think(thought="…")` — no-op scratchpad (shown in chat as "Spickzettel").
  Obtains no information and changes nothing; the thought is appended to the
  turn's tool history so the model can re-read it on later rounds. Unlike a
  model's native reasoning field (generated per round, then discarded), a `think`
  note persists across tool rounds — use it after a tool result to check the
  result against the relevant policy/constraint before acting. In-prompt on a
  classifier-gated turn ONLY when the model's `scratchpad_mode` is not `off`
  (conditional floor — see `scratchpad_mode` below); on `off` it is deferred like
  any other unflagged tool (still `tool_search`-discoverable, never removed).
- `sequential_thinking(thought, thoughtNumber, totalThoughts, nextThoughtNeeded, …)`
  — the FULL upstream-MCP scratchpad (shown as "Erweiterter Spickzettel"). Same
  no-op nature as `think` but with structured bookkeeping: numbered thoughts, a
  running total, an explicit nextThoughtNeeded flag, and revision/branch tracking
  (isRevision/revisesThought/branchFromThought/branchId/needsMoreThoughts). State
  (thought history + branches) is per-request in RequestContext._dynamic — NOT
  process-global like the upstream server (that would leak across sessions).
  Returns a status JSON {thoughtNumber, totalThoughts, nextThoughtNeeded,
  branches, thoughtHistoryLength}. Same conditional floor as `think`. Its wire
  description is kept VERBATIM from the upstream MCP server (incl. the 11-point
  "You should:" list) on purpose: that procedural guidance is what makes a model
  call the tool MULTIPLE times (numbered thoughts) instead of once — an
  abbreviated description made gemma-12B call it 1× (no decomposition), the full
  one makes it call 3×. Don't trim it despite prompt-bloat instincts.
- `calibrate(task, facts, gaps, confidence, recommendation, [inferences,
  speculation])` — no-op calibration scratchpad (shown as "Kalibrier-Spickzettel";
  trimmed port of the metacognitive-monitoring idea from
  waldzellai/model-enhancement-servers). Instead of "think first" it forces
  "check whether you actually KNOW": the model must split its planned answer
  into facts (read in documents this conversation, with source) / inferences /
  speculation, list gaps, give a confidence 0-1 and a recommendation
  (`answer` | `answer_with_caveats` | `refuse`) — and then follow that
  recommendation in the final answer. The only real logic is a deterministic
  consistency check (recommendation=answer with empty facts → flagged back in
  the tool result). Deliberately flat string-array schema (weak local models
  can't fill nested object arrays). UNLIKE think/sequential_thinking it is NOT
  in the structural floor and NOT in-prompt by default: statically deferred via
  tool_settings (interactive=deferred) and pulled in-prompt per turn (via
  undefer) ONLY when the model's `scratchpad_mode` is `calibrate` — every other
  mode's wire stays byte-identical.
- Per-model `scratchpad_mode` (config + Models-tab dropdown "Spickzettel"):
  `off` | `simple` | `sequential` | `calibrate` | `auto`. It gates TWO things,
  and both follow the mode (v9.312.1 — before that only the first did, so `off`
  suppressed the request but still handed the model the tools, and strong models
  called them unprompted): (1) the wire-only "think first" REQUEST, and (2) the
  tool FLOOR — `brain.tool_gating_floor(model)` adds `think`/`sequential_thinking`
  to the structural floor (`tool_search`/`ask_user`) only when the mode is not
  `off`. The floor is keyed on the STATIC model config, so it is identical on
  every turn of a model (incl. `auto` turns the classifier answers "off" for) →
  KV-prefix-stable; only changing the dropdown invalidates the prefix. On a fixed mode, every
  turn appends a wire-only request (FORCE_THINK_PROMPT /
  FORCE_SEQUENTIAL_THINKING_PROMPT / FORCE_CALIBRATE_PROMPT, on the last user
  message — KV-stable, same mechanism as caveman) telling the model to call the
  matching scratchpad tool before answering; the other scratchpad tools are
  hard-excluded that turn (weak models bleed fields between coexisting
  scratchpad tools). On `auto`, `resolve_scratchpad_choice(analysis)` decides
  per turn from the classifier's task_types + complexity: synthesis/reasoning at
  medium/high → simple; very hard multi-facet reasoning (high + ≥2 reasoning
  types) → sequential; lookups / low complexity / fast / reporting-only → off.
  `auto` NEVER picks `calibrate` (the classifier has no refusal signal).
  DISTINCT from the model's thinking level (native reasoning); both can be on at
  once. Grounded in two gemma-12B evals: a scratchpad lifts multi-doc synthesis
  a lot (simple 0.92 vs off 0.55 bucket mean), sequential is steadier but never
  better in the mean at ~2.8× time; `calibrate` is the REFUSAL SPECIALIST —
  refusal-bucket total 0.61→0.83 and refusal axis 0.07→0.67 vs off, but it
  regresses broad multi-doc synthesis (M2 0.93→0.62 with occasional turn stalls)
  — so use it for policy/compliance Q&A surfaces where "the documents don't
  answer this" must be said out loud, NOT as the general default. Recommended
  `auto` for weak local models, `off` for cloud (token cost). Legacy
  `force_think`/`force_sequential_thinking` booleans map to `simple`/`sequential`
  and are dropped on next save.

## Tool group → name map (groups in `agent.json → tool_groups`)

```
core          read_file write_file edit_file list_directory search_files
              execute_command tool_search ask_user
documents     read_document write_document edit_document render_diagram
              xlsx_inspect xlsx_query xlsx_create xlsx_edit xlsx_diff text_diff
              data_query db_query
ocr           ocr_inspect ocr_extract ocr_region ocr_fields ocr_tables
doc_checks    mrz_verify doc_dates_check identity_consistency
memory        mempalace_query save_chat_to_memory
              mempalace_kg_query mempalace_kg_search mempalace_kg_neighbors
wiki          wiki_write wiki_read wiki_delete wiki_structure
context       context_search context_detail context_recall
web           web_fetch exa_search searxng_search
              science_search dev_search image_search news_search
email         email_accounts email_inbox email_read email_search email_send email_reply
delegation    delegate_task task_status task_cancel
background     run_background_task retry_background_task
code_graph    code_search code_trace code_query code_snippet ast_grep_search ast_grep_replace
git           git_command github_command git_worktree
scheduler     schedule_list schedule_history
mcp           mcp_connect mcp_disconnect mcp_servers
skills        use_skill
nodes         list_nodes
thinking      think sequential_thinking calibrate
code_exec     python_exec r_exec kernel_exec kernel_status kernel_restart
audio         transcribe_audio generate_audio_overview
translation   translate_text translate_document detect_language
              list_glossaries get_glossary
workflows     ask_user_for_file ask_llm agent_step
workers       get_artifact_detail   (worker_* control tools removed 2026-07-13)
image_gen     generate_image
```

Default-enabled groups: `core, memory, context, web, delegation, git,
skills, nodes, scheduler, mcp, workers, translation`.
