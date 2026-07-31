---
name: KG extraction quality limited by MemPalace drawer chunking — SHIPPED
description: SHIPPED 2026-04-26 — `kg_extract.run_kg_post_pass(chunking_mode="source_file")` re-chunks the original markdown at 3500 chars with paragraph boundaries instead of feeding miner drawers 1:1. Validated 70× yield (430 triples from one bank-policy PDF vs 6 in per-drawer mode).
type: project
originSessionId: 960d069c-8b9b-4582-a9f7-c2c3d1d52ba1
---
**SHIPPED — kept for historical context.**

**Fix shipped 2026-04-26:**

- Added `chunking_mode` param to `kg_extract.run_kg_post_pass`. Default `"source_file"`.
- Source_file mode iterates **distinct source files** (not drawers) via `_iter_wing_source_files`, reads each `.md` from disk, strips brain-source frontmatter, paragraph-chunks at `source_chunk_chars` (3500 default).
- Cursor key changed from `<drawer_id>` to `<rep_drawer_id>#<chunk_index>` so re-runs only re-extract failed chunks. No schema migration — encoded in existing `source_drawer_id` text column.
- Triple provenance attaches to the *first* drawer id from each source group, plus the source_file. Close enough for "open the source" workflows.
- Falls back to per-drawer mode for sources whose file isn't on disk.

**Validation:**
- Same bank-policy PDF: 6 triples per-drawer → **430 triples source_file** (~70× improvement).
- 9.8 triples/chunk average — matches the prompt-eval baseline exactly.
- 4 errors (8% chunk failure, malformed JSON edge cases).

**Default model: `gemini-2.5-flash`** (not local) because of an unrelated GPU memory issue: chat warmpool pins 26B at 22GB, leaves no headroom for e4b/26B extraction without raising oMLX's 25.6GB process cap. Switching to gemini bypasses GPU contention entirely; ~10s/chunk warm.

**Knobs added to `mempalace.kg`:** `chunking_mode` (`"source_file"|"per_drawer"`), `source_chunk_chars` (default 3500). Per-drawer mode kept for callers that want it.

**Original analysis below — preserved for context:**

When step 1 KG extraction was validated against the bank-policy PDF post-auto-conversion, the daemon successfully converted the PDF and mined 187 drawers, but only ~2-3% of drawers produced triples — far below the prompt-eval baseline of ~25-30% on the same PDF.

**Why:** the MemPalace miner's chunker splits prose mid-sentence at ~700-800 chars, producing drawer content like `"ürfen auf Grund der oben genannten Verordnung ab dem 1. Februar 2016 nicht mehr verwendet werden..."` (note the cut-off `dürfen` at the start). The LLM seeing word-fragments and broken sentence starts conservatively returns `[]` even when the rest of the chunk contains clear obligations / citations / effective-from claims.

**Evidence:** running the same content through `extract_triples_from_drawer` confirmed empty responses on chunked drawers, while running the same content joined into a 4000-char block produced 3 high-quality triples in ~2s. The model is capable; the chunk size is the constraint.

**How to apply:** when revisiting KG extraction quality:

1. **Don't fix in the miner** — its chunk size is tuned for vector retrieval, not for LLM claim extraction. Different goals, different optimal sizes.
2. **Fix in the extractor**: `kg_extract.run_kg_post_pass` should chunk *its own input* rather than pass each MemPalace drawer 1:1 to the LLM. Two options:
   - **Stitch adjacent drawers** by `source_file` + sequential drawer order, target ~3000-4000 chars per LLM call. Run extraction on the stitched window, attribute triples to the *first* drawer's `source_drawer_id` (or to the source_file with no drawer-level provenance — debatable).
   - **Bypass drawers entirely** for prose extraction: have the extractor read the original `.md` from disk via the source_file, chunk it itself with paragraph-aware breaks (the same logic in `kg_prompt_eval.py`'s `chunk_text`), extract per chunk. Drawer ids then drop from triple provenance — only `source_file` + line/page anchor remains. Cleaner, but breaks the "every triple links to a drawer" model.
3. **The `.brain-extracted/` markdown is on disk and accessible** — the extractor can re-read it. So option 2 (re-chunk from source) is implementable without daemon changes.
4. **Output volume sanity-check**: prompt-eval gave 28 triples per 3 substantial 4000-char chunks ≈ 9 triples/chunk. With 65 chunks per PDF and that density, we'd expect ~150 triples per PDF, not ~5. Big regression from the chunking.

Doesn't block step 1 — the auto-conversion + daemon hooks + scoping all work. Quality knob, not a correctness bug. The system still works on hand-fed content (the prompt-eval still produces 28 triples on the same PDF when the chunker is paragraph-aware).
