---
name: Data section §16/§17 implementation kickoff
description: Scope + ordering for building the Data Workbench anonymisation (§16) + file-level GDPR scan (§17) — start here in a fresh session
type: project
originSessionId: 12f61ba2-5788-4a5c-8d8a-6e7a79c039b4
---
Plan doc: `data-section-plan.html` in repo root (proposal only — nothing built). Relevant: §10 phases, §16 anonymisation, §16.7 use-case gallery, §17 file-level GDPR scan, §11 file-by-file list.

**Why:** the standalone Excel-Anonymisierung SPA the user uploaded is being absorbed into Brain as the manual half of `data_anonymise`; user wants it inside the perimeter (audit log, RBAC, 71-detector scanner, artifact pipeline). Anonymisation must be deterministic Python, NOT LLM calls (Rule 5). GDPR scanner must also work at file granularity → tell the user which files leak, offer auto-fix or manual-via-GUI.

**Hard dependency:** §16/§17 sit on Data Phase A (DuckDB-per-session store, `handlers/data_viz.py`, `engine/tools/data_viz.py`, `web/js/data.js`, the `data_viz` tool group). Phase A is NOT built. So the realistic first PR is *Phase A skeleton + xlsx/csv-only anonymise + manual panel* — not all of §16+§17.

**How to apply — first shippable PR (Rule 2 scope cut):**
1. Data Phase A minimum: session DuckDB file, `POST .../upload` → table + scanned sample, `data_query` tool, one web view. (See §3 reuse map, §6 API, §8 data store.)
2. `tool_data_anonymise` — xlsx/csv only, strategies: hash / tokenise / redact / generalise / nullify / shuffle (defer fpe, noise if time-boxed). `output_format`: `preserve` (csv→csv; xlsx→xlsx needs translate.py's OOXML rewrite path — if not wired, ship csv-preserve + fresh-xlsx and note it) / `csv` / `xlsx` / `markdown`. Invariants: never mutate source, new DuckDB table + new file, re-run the 71-detector scanner on output → `residual_scan`, write 3-sheet index file (`mapping`/`schema`/`info`) when a reversible strategy is used. Audit line per run.
3. `mode="deanonymise"` arm — file + index in → restore reversible strategies, return `not_reversible[]`.
4. `POST .../anonymise` + `POST .../deanonymise` + `GET .../mapping/<file>` (download = audit event) — thin no-LLM endpoints into the tool body.
5. The §16.0 manual GUI panel (pick columns + per-column strategy + output format + Mode toggle) → POSTs to those endpoints.

**Defer to later PRs:** docx/pptx/pdf format arms (need Translation Phase B OOXML path confirmed wired) · `data_scan_files` + §17 triage view · `auto_anonymise` config enum + the §16.7-B/C/D automatic paths (auto-modifying on a scanner hit needs false-positive rate measured on real bank tables first — explicit in the plan's Rule-2 callout).

**Reuse, don't rebuild:** 71-detector scanner (in prod, v8.x — `PIIScanner` in web/index.html mirrors `_pii_*` in brain.py), OOXML chunked rewrite (`handlers/translate.py` Phase B), PDF text + scanned-PDF OCR (`doc_convert.py` unified pipeline), artifact pipeline, audit log, RBAC, quota/provider routing. The plan's whole premise: zero core-path changes.

**Process the user wants:** analyze + plan before coding; single choke point not per-caller patches; produce an implementation plan scoped to ONE PR and get alignment before touching code.
