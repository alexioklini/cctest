---
name: tool_mempalace_query swapped to direct Chroma query — root-cause fix for hallucination on policy corpus
description: 2026-04-29 — replaced search_memories() with col.query() (mirrors vanilla `mempalace search` CLI); v8.21.2's closet-boost workaround was masking a deeper bug in MemPalace's hydration path
type: project
originSessionId: 5b1a5332-c623-49a6-99c5-a5d75fd2a5ad
---
After 12+ hours of debugging Brain's hallucination on the IT-Risk Score query, the root cause turned out to be `tool_mempalace_query` calling MemPalace's `search_memories()` instead of `search()` (which is what the `mempalace search` CLI uses).

**The two functions diverge fundamentally**:

`search()` in `mempalace.searcher.py:239` — calls `col.query(query_texts=[...], n_results=N)` directly on Chroma. Returns N raw vector hits, naturally diverse across the corpus because Chroma's distances are per-chunk.

`search_memories()` in `mempalace.searcher.py:304` — runs an additional "closet-boost + drawer-grep-enrichment" hydration pass. This pass has a structural bug on **multi-chunk sources where the document title appears in every chunk's frontmatter**: the closet boost pulls the same source for ~all top hits, then re-runs `set(query_tokens) & set(chunk_tokens)` to pick a chunk per hit — and since every chunk contains the title, every chunk ties at score 1, ties resolve by iteration order, and chunk 0 wins for ALL hits. Result: N byte-identical hits showing the same frontmatter blob.

**Documented in CLAUDE.md v8.21.2** (Malwareschutz incident): same bug, same symptom. v8.21.2's "fix" was a Brain-side workaround in `tool_mempalace_query` that deduplicated by source_file and tried rare-term substitution to recover the right chunk. Worked for the Malwareschutz case (token "spam" was rare). Did NOT work for "IT-Risk Score Berechnung" because every query token has a structural match in the German source filename `20_2_1_2_ARL_ISMS Risikomanagement Handbuch.pdf.md` — `risk` ⊂ `Risikomanagement`, `score` is in the title, leaving no rare tokens.

**The simple fix that should have been the first answer**: skip `search_memories()` entirely and call `col.query(...)` directly, just like the vanilla CLI does. This is what `tool_mempalace_query` does now.

**Verification (2026-04-29 ~15:00)**: query "IT-Risk Score Berechnung" against `project__f201b24ff6a2`:
- Before fix: 1 drawer, all frontmatter, no Section 2.13 body, no Prozent-zu-Score-Tabelle → model hallucinated
- After fix: 5 drawers, similarities 0.69/0.625/0.612/0.586/0.536, drawer [3] contains `ITRMP erlaubt es... Erfüllt=100%, Teilweise=50%, Nicht erfüllt=0%` → matches vanilla MemPalace output byte-for-byte

**Performance impact**: net positive. `search_memories()` did ~5 extra Chroma reads per call (closet lookup + per-source chunk fetches for the substitute path). `col.query()` is one Chroma read.

**Trade-offs accepted**:
- We give up the closet-boost ranking. On this corpus and these queries it was making things worse not better, so net positive. If a query needs closet boost (e.g. cross-document semantic linking via topic tags) we'd notice and could add it back optionally.
- We give up the drawer-grep-enrichment that picks the "best" chunk among multiple from the same file. Pure Chroma vector ranking already does this naturally for diverse documents; the enrichment was only patching the title-collision case.
- We give up `total_before_filter` being meaningful — now it equals `len(raw)`, since we're not over-fetching for a filter pass.

**Brain-side dedupe rewrite (also 2026-04-29)**: even with the chroma-direct path, kept a softer dedupe: by `(source_file, content-fingerprint)` with `max_per_source=4`. This catches genuine identical hits if Chroma ever returns them and prevents one document from monopolizing all 5 result slots. The aggressive earlier "1 hit per source" dedupe was discarded.

**Substitute logic kept but rewritten**: word-boundary matching (`\bRISK\b` doesn't match `Risikomanagement`) so German compounds don't falsely dismiss a token as "in the filename". Only fires when the chroma-vector path returns a hit whose text doesn't word-match a query token. Effectively never fires now since chroma-direct already returns relevant chunks; kept as defensive fallback.

**How to apply**:

When a high-level wrapper provides too much "magic" (boosting, filtering, hydration) and is producing wrong results, **try the lowest-level primitive first** before adding workarounds. The `mempalace search` CLI worked perfectly because it took the simplest path. Brain spent 12+ hours building workarounds for a problem that was solved by removing the wrapper entirely.

**Related memories**:
- `project_kg_vs_vanilla_mempalace_regression.md` — the original "vanilla works, Brain hallucinates" report
- `project_kg_disabled_markitdown_swap.md` — earlier same-day fix that didn't fix the underlying issue (was three independent improvements)
- `project_drawer_path_resolution_fix.md` — earlier same-day fix for read_path/source_file basename issue
- `bug_summarise_tool_result_unpack.md` — earlier same-day fix for the 2-vs-3-tuple crash that masked tool errors as hallucination
- `project_it_risk_score_canary_answer.md` — gold-standard canary answer to compare against
