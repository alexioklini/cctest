---
name: KG disabled + markitdown converter + read-document mandate (regression fix)
description: 2026-04-29 — three coordinated changes to address vanilla-MemPalace-vs-Brain regression: KG off, markitdown for PDF→md, system prompt mandates read_document after mempalace_query
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
On 2026-04-29 the user reported Brain's KG-augmented retrieval hallucinated on IT-Risk Score query while vanilla MemPalace + markitdown gave correct answers (see `project_kg_vs_vanilla_mempalace_regression.md`). Three coordinated changes shipped to address all three suspected causes simultaneously rather than the slow one-at-a-time isolation path.

**1. KG disabled in `mempalace.kg.enabled = false`**:
- `_kg_resolve_project_scope` (claude_cli.py) now also checks `kg.enabled`; refuses with a helpful message pointing the model at `mempalace_query + read_document` instead.
- Project-sync daemon's KG post-pass already had a `kg.enabled` gate (server.py:13248).
- Stale 713 vibe-fast triples remain in MemPalace's KnowledgeGraph DB but are unreachable from agent tools; harmless. Will purge on next re-enable via `/v1/mempalace/kg/reextract`.
- Settings → Knowledge Graph tab still shows the config (admin can flip back on).

**2. Markitdown wired into `doc_convert.py` as preferred backend**:
- New `_extract_with_markitdown(path)` shells out to `/opt/homebrew/bin/markitdown` (subprocess; CLI is on PATH).
- `convert_folder(use_markitdown=True)` tries markitdown first for `.pdf/.docx/.pptx/.xlsx`; falls through to legacy fitz/python-docx/python-pptx/openpyxl on any failure.
- Frontmatter records which backend was used: `<!-- brain-converter: markitdown -->` or `fitz/legacy`.
- Top-level config knob: `conversion.use_markitdown` (default `true`); plumbed to the daemon via `_conv_use_markitdown()` reading `engine.CONFIG_PATH` per-cycle.
- `kg-real-policies` validation result: 53/58 files converted with markitdown (15 IT + 38 Datenschutz), 5 fallbacks (4 German vendor-questionnaire .docx files + 1 .xlsx audit plan — markitdown crashes on those specific files; fitz path picked them up cleanly). 1,449 drawers indexed (was 1,360 with fitz only — markitdown produces denser text per file).
- Markitdown timeout: 120s/file. Plenty for typical PDFs.

**3. System prompt mandates `read_document` after `mempalace_query`** (claude_cli.py `_build_system_prompt`):
- New "MANDATORY 3-STEP FLOW" block: (1) call mempalace_query, (2) read_document on each top drawer's source_file, (3) answer ONLY from what was read in step 2.
- Explicitly tells the model that drawer text "is a pointer, not a quotation" — answering from drawer snippets alone is the documented hallucination cause from the 2026-04-29 regression.
- KG section now wrapped in `if _kg_enabled_for_prompt:` — when KG is off, the prompt simply omits the kg_search/kg_query advertisement and tells the model only mempalace_query + read_document are available.
- Citation discipline + REFUSAL DISCIPLINE blocks unchanged.

**Why all three at once (not the slow isolation path I'd previously recommended)**: user opted for action over investigation. Risk: if the test improves, we won't know which of the three was the dominant fix. Mitigation: the changes are independent and toggleable — `mempalace.kg.enabled` and `conversion.use_markitdown` are runtime knobs, the system prompt change is a code revert. Reproducing the regression to attribute is one config flip + restart away.

**How to verify the fix worked**:
- Open a chat in the `kg-real-policies` project.
- Ask: "Wie wird der IT-Risk Score berechnet?" or similar question whose answer is in the back half of the ISMS Handbuch (the canary used 2026-04-28 + 04-29).
- Expected: model calls `mempalace_query`, then calls `read_document` on the top drawer's source_file, then answers from the file content with citation `[Quelle: 20_2_1_2_ARL_ISMS Risikomanagement Handbuch.pdf]`.
- Failure modes to watch for: (a) model answers from drawer text alone without calling read_document — system prompt didn't take effect; (b) model still hallucinates despite reading the file — converter quality is the dominant issue and the regenerate confirms whether markitdown alone suffices; (c) model still reaches for `mempalace_kg_*` — the KG disable didn't propagate or the model is ignoring the refusal message.

**Operational state at handoff** (2026-04-29 ~12:40):
- Config: `mempalace.kg.enabled=false`, `conversion.use_markitdown=true`.
- Wing: `project__f201b24ff6a2` rebuilt clean (1,449 drawers, all from markitdown-or-fallback markdown).
- Closets: 60 dropped, will rebuild on next mining pass via mp_miner.
- Stale triples: 713 still in `knowledge_graph.sqlite3` (unreachable, inert).
- Backup of pre-change state: not taken — the rebuild is reversible by re-enabling KG (`POST /v1/mempalace/kg/config {enabled: true}`) + reextract; the markitdown swap is reversible by toggling `conversion.use_markitdown=false` + wiping `.brain-extracted/` to force re-conversion with fitz.

**Discovered along the way**:
- The "startup wipe" of project drawers was removed from server.py (no source matches; only log message remains from older binary). Wing-level purge has to be done manually via Chroma `col.delete(where={"wing": ...})`. Wrote an inline script during this session; could productize as a `/v1/projects/<name>/wipe-drawers` admin endpoint.
- Markitdown subprocess returns exit 0 even when crashing with a Traceback to stderr — exit-code check alone is insufficient; the wrapper relies on empty-stdout detection. If markitdown ever starts printing tracebacks to stdout instead, the fallback would silently degrade. Worth tracking if markitdown updates.
