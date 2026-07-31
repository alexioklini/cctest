---
name: project-attachment-anonymisation-redesign
description: "Redesign of attachment anonymisation — one seam in (read_document/read_file), one seam out (_after_file_write). Spec + implementation 2026-05-16."
metadata: 
  node_type: memory
  type: project
  originSessionId: 1e8c28f2-85ba-4b6d-b92b-c50274f0e96b
---

Redesign of the transparent-anonymisation attachment path. Implemented
2026-05-16. Live verification: POST /v1/attachments/scan returns 3 findings
(email/iban/phone) for PII-laden text, `reason:"media"` for PNG,
`reason:"archive"` for ZIP. 53 existing tests still pass.

**Why:** current implementation has format-specific walkers (`engine/file_pseudonymize.py`
with docx/pptx/xlsx/csv/plain walkers) that rewrite binaries in place
pre-LLM. Brittle, refuses PDFs, misses cross-run OOXML PII, doesn't
compose. Text path is clean (`pseudonymize_text` in, `StreamingDeanonymizer`
out); attachment path should follow the same shape.

**Architecture (target):**
- One seam in: `tool_read_document` + `tool_read_file` scan + pseudonymise
  the extracted text using the session mapping before returning to LLM.
- One seam out: `_after_file_write` reverse pass (already exists, kept).
- File on disk NEVER rewritten anymore.

**Locked decisions:**

| Question | Decision |
|---|---|
| When does modal fire for file PII? | **Pre-send (option 2b)** — scan at upload time, modal shows combined findings before send. |
| Cache scan vs rescan inside read_document? | **Rescan** inside read_document — simpler, cheap on extracted text. |
| Modal grouping | **Group by source** (`Typed message: …`, `report.pdf: …`). |
| Archives (.zip/.epub/.msg) | **Skipped**, treated like images — accepted gap. |
| Upload scan cap | **30s timeout + 50MB file size**. |
| Cap-exceeded behaviour | **BLOCK sending** until user removes the file (unscanned ⇒ unknown PII). |
| Empty mapping when anonymise picked but no findings | **Always create** when user picks anonymise — covers later-found PII in tool reads. |
| `tool_read_file` (arbitrary disk paths) | **Wrap** — mapping flows through, streaming deanonymizer reverses on reply. |
| Re-upload in later turn | **Re-scan** — cheap, no stale-cache bugs. |
| Images / audio / video | **Accepted gap** — same as upfront scanner coverage. |

**Edit sites (dependency order):**
1. `brain.py` — new `_pseudonymise_tool_output_text(text, source)` helper.
   Call from `tool_read_document` (each parser branch) + `tool_read_file`.
2. `brain.py` — factor parser dispatch out of `tool_read_document` so the
   upload-scan endpoint shares the extraction logic.
3. New endpoint `POST /v1/attachments/scan` (handlers/chat.py or new
   handlers/attachments.py): multipart upload → save → extract (30s/50MB
   cap) → scan → return findings. Returns `{attachment_id, source_name,
   findings, categories, finding_count}` or block-error on cap exceeded.
4. `web/index.html` + `web/js/chat.js` — call scan endpoint on attach,
   cache findings per attachment, badge composer, merge with typed-text
   findings on send, group modal by source. Block-send if any attachment
   reports unscannable.
5. `handlers/chat.py:_handle_chat` worker — create empty mapping eagerly
   when `gdpr_action="anonymise"`. DELETE the file-rewrite block
   (~lines 1648–1670) and the FilePseudonymizeError recovery branch.
6. DELETE `engine/file_pseudonymize.py`.
7. `pseudonymizer.py` — DELETE `pseudonymize_file` + SUPPORTED_EXTS
   reference. KEEP `deanonymize_file` (used by `_after_file_write`).
8. DELETE / repurpose `tests/test_pseudonymizer_files.py` —
   roundtrip cases move to "read_document returns pseudonymised text +
   write_file restores it" via the new seam.

**Out of scope:** images / audio / video / archives — accepted gap, same
as upfront scanner coverage.

Related: [[project_transparent_anonymisation_complete]] (current v8.5.0
implementation that this replaces).
