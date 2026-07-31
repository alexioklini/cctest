---
name: project_doc_extraction_unified
description: "2026-05-21 (v9.10.0) all document extraction unified onto one _do_extract dispatcher; two tuning surfaces (markitdown + _extract_*), per-format reorder = one-line _MARKITDOWN_EXTS edit"
metadata: 
  node_type: memory
  type: project
  originSessionId: 0cae8cce-7944-4896-99af-c099409dd176
---

v9.10.0 (2026-05-21): every document read in Brain — chat read
(`tool_read_document`), project mining (`convert_one`), PII pre-send scan
(`extract_attachment_text`), ARL classification scan
(`handlers/classification`) — now funnels through ONE dispatcher:
`engine.doc_convert._do_extract`. Replaced 4 independent xlsx readers (+ docx/
pptx duplication). Fixed a latent bug: read paths lacked the pipe-escaping +
newline-stripping `_extract_xlsx` had, so a cell with `|` or `\n` corrupted the
markdown table the model saw.

**Two tuning surfaces per format** (the user's explicit design goal):
1. **markitdown** (subprocess, tried first) — gated by `_MARKITDOWN_EXTS`.
2. **`_extract_*` Python fallbacks** in doc_convert.

**Per-format reorder/disable = one line**: to force a format onto our own code
(markitdown loses), remove its ext from `_MARKITDOWN_EXTS`. True fallback
*inversion* (own-first-then-markitdown) is NOT wired — `_do_extract` is
hardcoded markitdown-first → fallback. The disable-via-set covers the
"markitdown worse" case without a logic change. **Why:** user wants exactly two
fine-tune areas for multi-user xlsx/pdf research bugs. **How to apply:** point
fixes at markitdown or the relevant `_extract_*`; never add a per-consumer
parser (that's the duplication this removed).

`.eml` deliberately NOT in `_MARKITDOWN_EXTS` — empirically tested 2026-05-21:
markitdown handles eml but worse (leaks `Content-Type:` MIME headers into body);
stdlib `_extract_eml` produces clean `# Subject` + `**From:**` markdown.
`.txt/.md/.html/.json` skip markitdown too (already text).

**caps byte-stability invariant**: `caps`/`sheet`/`slides`/`pages`/`emit_meta`/
`page_marker` knobs default to mining's prior behavior so the daemon's
companion-`.md` output stays byte-identical (else every project doc re-embeds).
Knobs only bite on the *fallback*; markitdown success returns verbatim. Read
paths: `caps=False`. Mining/classification: `caps=True` (100k rows, 200 ch/cell).

**Likely real-world failure mode under multi-user load**: markitdown is
`subprocess.run` per call (120s timeout) with NO concurrency cap (unlike
`LocalProviderQueue`). Predict process-pressure/timeout, NOT extraction-logic
bugs — fix would be a concurrency gate (a 3rd lever, separate from the two
content surfaces). Tempfiles use unique names; ad-hoc `.md` cache keyed by
`(abs_path, mtime, size)` — concurrent same-file reads safe.

`tool_read_attachment` deleted (was unreachable — never in TOOL_DISPATCH/
DEFINITIONS/GROUPS, store never populated). `DocumentParser.parse_{docx,xlsx,
pptx}` are now thin shims over `_do_extract`. Full detail in engine/CLAUDE.md
"Document Extraction Pipeline". See also [[feedback_single_fix_point]],
[[project_unified_extraction_pipeline]].
