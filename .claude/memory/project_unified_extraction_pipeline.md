---
name: Unified document extraction pipeline (markitdown + OCR fallback)
description: 2026-05-07 — single extraction path for binaries (project files + chat attachments + ad-hoc reads) via doc_convert.convert_one(); markitdown → fitz → Mistral OCR or local-vision OCR for scanned PDFs
type: project
originSessionId: 67cd8fcb-fff7-4609-99f9-6da618639733
---
2026-05-07 — Unified document extraction shipped. All binary reads (PDF/DOCX/PPTX/XLSX/MSG/EPUB/ZIP) flow through `engine/doc_convert.py:convert_one(src, project_root=None)` regardless of origin. Same pipeline for project input folders (mined by daemon), chat attachments, and ad-hoc `read_document` calls — eliminates the dual-path inconsistency that caused the 2026-04-29 hallucination chain.

**Path resolution:**
- Project files (parent contains `.brain-extracted/`) → `<project_root>/.brain-extracted/<rel>.md` (existing daemon layout, byte-identical reuse)
- Ad-hoc files → `~/.brain-agent/extracted-cache/<sha256(abs_path:mtime:size)>.md` with 30-day atime LRU eviction (runs once per project-sync cycle)

**Extractor cascade (in `_do_extract`):**
1. **markitdown** (CLI subprocess) for `.pdf/.docx/.pptx/.xlsx`
2. **per-format extractor** (fitz / python-docx / python-pptx / openpyxl / email / extract-msg) — fallback when markitdown empty/fails
3. **OCR** for `.pdf` only when both above produce empty output (scanned image-only PDFs)

**OCR engines** (`config.json → ocr.engine`):
- `mistral_ocr` (default) — Mistral OCR API via `mistral-experimental` provider, $0.001/page, ~2.5s/page, excellent table fidelity
- `local_vision` — render PDF page → image (200 DPI default) → OpenAI-compatible vision LLM (`local_vision_model`, default `gemma-4-26B-A4B-it-MLX-4bit` via oMLX), free, ~37s/page, decent tables but occasional column hallucinations
- `auto` — Mistral OCR first, fall back to local-vision on failure (groundwork for GDPR PII routing)
- `none` — disabled, scanned PDFs get an empty-marker companion

**Important — Mistral OCR API key scope**: `mistral-vibe` provider key is scope-restricted to Vibe and rejects `/v1/ocr` with HTTP 403 `api_key_scope_not_allowed`. Must use a standard Mistral API key (we use `mistral-experimental`).

**Cost tracking**: new `CostTracker.log_ocr()` method writes synthetic rows to `cost_log` with `tokens_in=pages`, `tokens_out=0`, explicit `cost_usd`. Daemon-thread calls fall back to `agent='main', session_id='', user_id=''` since thread-locals aren't set there; chat-thread calls populate normally.

**Per-cycle page cap**: `ocr.max_pages_per_cycle` (default 1000) enforced via module-global `_ocr_pages_this_cycle` counter, reset by `reset_ocr_cycle_counter()` (not yet wired — daemons currently let the cap reset implicitly across calls; add explicit reset at cycle start if multi-day archives become a billing concern).

**Validated 2026-05-07** on `/Users/alexander/Documents/dev/ready2accounting/kontierung.pdf` (1-page scanned receipt categorization sheet):
- markitdown: 1 char (empty)
- fitz: 11 chars (whitespace)
- Mistral OCR: 1559 chars, perfect 3-column table
- Gemma-4 26B local-vision: 1793 chars, 4 columns (extra hallucinated column)

**Files touched**:
- `engine/doc_convert.py` — `convert_one()`, `evict_adhoc_cache()`, `_extract_with_mistral_ocr()`, `_extract_with_local_vision()`, `_do_extract()` cascade, `_log_ocr_cost()`
- `engine/tools/files.py` — `tool_read_document` routes binary formats through `convert_one()`
- `engine/analytics/costs.py` — `CostTracker.log_ocr()`
- `server.py` — eviction call once per `_project_sync_loop` cycle
- `config.json` — `ocr.{engine, provider, model, max_pages_per_cycle, cost_per_page_usd, local_vision_model, local_vision_render_dpi, local_vision_max_tokens}`
