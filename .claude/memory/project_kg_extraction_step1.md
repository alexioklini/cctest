---
name: KG triple extraction over project documents — step 1
description: 2026-04-26 shipped — LLM-based KG extraction over project documents. Auto-converts PDF/DOCX/PPTX/XLSX/EML/MSG to markdown, re-chunks source files at 3500 chars, extracts via gemini-2.5-flash. Validated 430 triples from one German bank-policy PDF, ~9.8 triples/chunk, controlled vocab holds at 98%.
type: project
originSessionId: 960d069c-8b9b-4582-a9f7-c2c3d1d52ba1
---
Step 1 of the knowledge-graph initiative is live. Per-project, project-sync daemon mines drawers as before; a follow-up post-pass calls a configurable LLM to extract `(subject, predicate, object)` triples and writes them to MemPalace's KG (`~/.mempalace/brain/knowledge_graph.sqlite3`).

**Why:** policy/spec/contract documents hold structured claims (obligations, citations, definitions, exceptions) that pure vector retrieval can't surface. The graph turns them into joinable rows for contradiction/coverage analysis without rescanning every document on every query.

**How to apply:** when working on KG-related issues, code or queries, the layout is:

- `kg_extract.py` — extraction module; `Profile` registry, `extract_triples_from_drawer`, `run_kg_post_pass`. Profiles ship: `normative` (default, controlled predicates: requires/forbids/cites/...), `generic` (open).
- `claude_cli.py` — three agent tools in the `memory` group: `mempalace_kg_query`, `mempalace_kg_search`, `mempalace_kg_neighbors`. All auto-scoped to the caller's current project; refused outside project context. Scoping uses `source_file` prefix matching against project-dir + every `input_folders[].path` (resolved through `realpath` to handle macOS `/tmp → /private/tmp`).
- `server.py` — daemon hook is `_run_kg_for(...)` inside `_project_sync_loop`; runs after each `mp_miner.mine()` per attachment hash and per input folder. HTTP endpoints at `/v1/mempalace/kg/{stats,wing,entity,extraction-log,config,reextract}`. Re-extract is admin or project-owner only and audit-logged as `kg_reextract`.
- `web/index.html` — Settings → Knowledge Graph tab (separate from MemPalace tab) with model picker, profile picker, per-project drilldown modal (`kgOpenProject`) showing predicate frequency bars + top entities + sample triples + recent extraction-log. Project Memory chip extended: shows "Memory: N indexed · M triples", pulses purple when extraction is running, double-click opens KG drilldown.
- `chats.db` — two new tables (idempotent): `kg_extraction_progress` (per-drawer cursor, makes re-runs O(1) on already-processed drawers) and `kg_extraction_log` (one row per run for the UI's recent-runs panel).
- `config.json` → `mempalace.kg`: `enabled`, `extraction_model` (default `gemma-4-e4b-it-4bit`), `profile` (default `normative`), `scopes` (default `[projects]`), `max_triples_per_drawer`, `min_confidence`, `max_drawer_chars`.

**Validation 2026-04-26 (final, after re-chunking + auto-conversion shipped)**: ran the full daemon cycle on the real German bank-policy PDF (`Richtlinie-ZV-Vordrucke-2016`). PDF auto-converted by `doc_convert.py` (no manual step), re-chunked at 3500 chars by `kg_extract`, extracted via `gemini-2.5-flash`. Result: **44 chunks → 430 triples in 950s**, ~9.8 triples/chunk, 4 errors (8% chunk failure, mostly malformed JSON edge cases).

Predicate distribution from this PDF:
- `requires` × 191, `cites` × 55, `permits` × 53, `forbids` × 33, `defines` × 30
- `condition` × 23, `applies_to` × 8, `exception` × 7, `penalty` × 3, `effective_from` × 1, `supersedes` × 2
- Off-vocab leakage: ~2% (e.g. `requires_pre_coding`, `is_intended_for`)

Subjects/objects stay verbatim German; predicates stay English. Examples:
- `(Mittelfeld) requires (prüfzahlgesicherte Kunden-Referenznummer gemäß ISO/CD-1164911)`
- `(Vordrucke) forbids (Werbetexte oder -motive)`
- `(Richtlinien 2016) supersedes (die bisherige Fassung)`
- `(Neue Richtlinien) effective_from (1. Februar 2016)`

The same KG also picked up 64 triples from /qb's CLAUDE.md / README.md — proving the `normative` profile generalises beyond legal docs to architecture/spec content (`(terminal) requires (utf-8 to render)`, `(system) forbids (text editing widgets)`).

**Default model post-validation: `gemini-2.5-flash`**. Reasons:
- Followed the verbose normative prompt reliably (mistral-vibe-cli-fast returned `[]` on 50%+ of valid chunks)
- ~10s/chunk warm including provider-queue overhead
- No GPU contention with the local 26B chat warmpool
- Reasoning model — needs `inference_max_tokens=8000` to avoid "max_tokens exhausted" mid-JSON

**Why local models didn't work for this PDF**: oMLX `gemma-4-e4b-it-4bit` triggered HTTP 507 (`projected memory 27.18GB would exceed process limit 25.60GB`) every time, because the chat warmpool pins 26B at ~22GB and e4b's 5GB doesn't fit on top. Fix is host-side (raise oMLX memory cap or unpin one model) — captured as known operational tradeoff. Switching to gemini bypasses the issue.

**Known footguns / gotchas:**

1. **`/tmp` symlink**: macOS `/tmp` resolves to `/private/tmp`. The MemPalace miner stores resolved paths in drawer `source_file`. Every prefix-builder (daemon `_run_kg_for`, agent tool `_kg_resolve_project_scope`, HTTP `_kg_resolve_project_from_query`, `_handle_kg_stats_global`, `_handle_kg_reextract`, project sync's `total_triples` rollup) MUST resolve via `os.path.realpath()` before filtering — otherwise no drawers match. Adding a new prefix-builder somewhere? Same fix needed.
2. **MemPalace KG path is `<palace_path>/knowledge_graph.sqlite3`**, not `~/.mempalace/knowledge_graph.sqlite3` (which is the unrelated default of `KnowledgeGraph()` when called with no `db_path`). Always pass `db_path=` explicitly. There's a stray default-path file from earlier exploration; it's not the live KG.
3. **MemPalace 3.3.0 KG schema lacks `source_drawer_id` and `adapter_name`** (added in 3.3.3). `kg_extract` falls back via `TypeError` to the legacy 5-arg `add_triple` signature. Source-scoping then relies on `source_file LIKE prefix%` only — works because project input folders have unique absolute path prefixes. Upgrading MemPalace will let us additionally filter by adapter_name without schema migration (the migration is in MemPalace's `_migrate_schema` ALTER TABLE path).
4. **Connection-refused during oMLX cold-start**: when the daemon's first KG calls hit oMLX while the model is still loading, all calls fail with `urlopen error [Errno 61] Connection refused`. `extract_triples_from_drawer` retries up to 3× with 0.8s + 2.0s backoff specifically on connection-refused; other errors don't retry.
5. **Reasoning models need bigger token budget**: `gemini-2.5-flash` and `mistral-vibe-cli-latest` are reasoning models. `inference_max_tokens` defaults to 8000 in `extract_triples_from_drawer`; below that the model exhausts on internal reasoning before producing JSON.
6. **Source-file vs per-drawer mode**: `chunking_mode="source_file"` (default in config, in daemon) reads the original markdown from disk and re-chunks at `source_chunk_chars` (3500 default) with paragraph boundaries. ~10× yield over `per_drawer` mode (which fed MemPalace's 700-char drawer fragments directly to the LLM and got `[]` because chunks started mid-sentence). Per-drawer mode preserved as fallback.
7. **Code files are skipped** by `_is_code_path()` at extraction time and recorded as `skipped: code` in the cursor — handled by Brain's existing code graph (separate substrate). Unifying code-AST + prose-LLM extraction into one KG is captured as a separate backlog item.

**Auto-conversion module** (`doc_convert.py`): handles `.pdf` (fitz), `.docx` (python-docx), `.pptx` (python-pptx), `.xlsx` (openpyxl, full rows up to 100k/sheet), `.eml` (stdlib email), `.msg` (extract-msg, optional). Writes to `<folder>/.brain-extracted/<name>.<ext>.md` with frontmatter carrying source path + mtime + size. Idempotent (skip when source unchanged), stale-swept (drop md when source removed), per-file isolated (one bad file doesn't break the cycle). Daemon calls `sweep_stale → convert_folder` before every `mp_miner.mine()` for both ingested/ and input folders. System prompt nudge tells the agent to prefer `read_document` on the original binary for full fidelity (tables, page layout, complete spreadsheet rows beyond the preview).

**Not yet built** (outside step 1): chats and scheduled-task artifact extraction (step 2/3), code graph consolidation into MemPalace, OCR for scanned PDFs (currently empty extractions get a stub markdown so they don't retry every cycle).
