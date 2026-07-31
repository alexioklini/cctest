---
name: project_watch_mempalace_336_office_docs
description: "WATCH ITEM — when MemPalace 3.3.6 releases (adds office-doc support via markitdown in the miner), evaluate the overlap with Brain's own doc_convert pre-pass before adopting. Not yet released as of 2026-05-28."
metadata: 
  node_type: memory
  type: project
  originSessionId: df4a3267-b48f-4c45-866c-8b0ac73cdafd
---

2026-05-28: User flagged that **MemPalace 3.3.6** (not yet released; we're on 3.3.5) will add **office-document support via markitdown** built into the miner. **When it lands, evaluate the implications before relying on it** — do NOT assume it's a free win or a drop-in replacement.

**Why it matters — Brain ALREADY solves this, more capably, via its own pre-pass:**
- MemPalace's miner today only reads text/code (`miner.py READABLE_EXTENSIONS` = .txt/.md/.py/.json/.csv/… — NO .pdf/.docx/.pptx/.xlsx).
- Brain works around that with `engine/doc_convert.py` (the single extraction choke point, `_do_extract`): a pre-mine pass runs markitdown on `_MARKITDOWN_EXTS = {.pdf,.docx,.pptx,.xlsx,.msg,.epub,.zip}` → writes `.brain-extracted/<name>.<ext>.md` companions (idempotent by mtime+size) → the miner reads those `.md` (which ARE in READABLE_EXTENSIONS). Project-sync + chat attachments + ad-hoc reads all funnel through this. See engine/CLAUDE.md "Document Extraction Pipeline".
- Brain's pre-pass is RICHER than bare markitdown: OCR fallback for scanned/image-only PDFs (markitdown+fitz both yield empty there), per-format ordering (`_MARKITDOWN_EXTS` set controls markitdown-vs-own-code per ext; .eml deliberately excluded — stdlib is better), caps tuning (rows/cells), and the `<!-- brain-source -->` provenance comment.

**Questions to answer at 3.3.6 release:**
1. Does 3.3.6 add office exts to the miner's READABLE_EXTENSIONS and convert them itself? If so, Brain's `.brain-extracted/` pre-pass and the miner's new path would BOTH run → double work, or the miner picking the binary while doc_convert also made a companion (which wins? dedup by content hash may or may not collapse them).
2. Is MemPalace's markitdown invocation as good as Brain's? Almost certainly NO OCR for scanned PDFs, no per-format tuning → for image-only PDFs the miner would still get nothing while Brain's OCR path does. Don't lose OCR.
3. Could we DROP Brain's doc_convert pre-pass and let the miner do it? Only if 3.3.6 covers OCR + the formats + quality. Likely NOT worth it — keep the pre-pass, possibly keep office exts OUT of the miner's direct path so there's no conflict.
4. Re-pin decision: adopting 3.3.6 is a normal pip upgrade → REMEMBER the span venv patch must be re-applied ([[project_mempalace_venv_patches]]); check whether 3.3.6 rewrote miner.py/knowledge_graph.py.

Default stance: Brain's doc_convert is the more capable path; treat 3.3.6's office support as redundant-at-best until measured. Related: [[project_doc_extraction_unified]], [[project_unified_extraction_pipeline]], [[project_mempalace_venv_patches]].
