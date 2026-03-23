# Feature Proposal: Universal File Intelligence

**Status:** Proposal (P1)
**Author:** Brain Agent Team
**Date:** 2026-03-23
**Effort Estimate:** 10-12 days

---

## Problem Statement

Brain Agent can currently ingest only **5 text-based formats** (PDF, DOCX, TXT, MD, HTML).
The real world has spreadsheets, presentations, images, audio, and video. An agent that
can't read an Excel report, understand a screenshot, transcribe a meeting recording, or
extract text from a PowerPoint is severely limited.

Current gaps:

| Format | Ingest to QMD | Agent can read | Agent can create/edit |
|--------|--------------|----------------|----------------------|
| PDF | Yes (pymupdf) | No (garbage via read_file) | No |
| DOCX | Yes (python-docx) | No (garbage via read_file) | No |
| XLSX/CSV | No | No | No |
| PPTX | No | No | No |
| Images (PNG/JPG/SVG) | No | Vision API only (not indexed) | No |
| Audio (MP3/WAV/OGG) | No | No | No |
| Video (MP4/MPG) | No | No | No |

Additionally, when documents ARE ingested, the knowledge graph treats them as opaque
text blobs. A spreadsheet with financial data, a presentation with architecture diagrams,
and a PDF contract all become the same "ingested" node type with no structural metadata.

## Proposed Solution

### 1. Expand DocumentParser (10 new formats)

Extend the existing `DocumentParser` class with parsers for all common file types:

#### Office Documents
| Format | Library | Extraction |
|--------|---------|------------|
| **XLSX/XLS** | `openpyxl` | Sheet names, cell data as markdown tables, formulas, chart descriptions |
| **PPTX** | `python-pptx` | Slide text, speaker notes, slide titles, table content, image alt text |
| **CSV/TSV** | stdlib `csv` | Parse as markdown table with headers |

#### Images
| Format | Library | Extraction |
|--------|---------|------------|
| **PNG/JPG/JPEG/GIF/WEBP** | Vision LLM (Haiku) | Send image to vision model, get description + OCR text |
| **SVG** | stdlib XML parser | Extract text elements, title, desc, metadata |

#### Audio
| Format | Library | Extraction |
|--------|---------|------------|
| **MP3/WAV/OGG/M4A/FLAC** | `whisper` (OpenAI) or `faster-whisper` | Speech-to-text transcription |

#### Video
| Format | Library | Extraction |
|--------|---------|------------|
| **MP4/MPG/AVI/MOV/WEBM** | `ffmpeg` (extract audio) + `whisper` | Transcribe audio track |
| | + keyframe extraction + Vision LLM | Describe key visual frames |

### 2. Smart Document Tools (read + create/edit)

New tools that understand document structure, not just raw text:

#### `read_document`
Format-aware document reader (replaces `read_file` for non-text files):

```json
{
  "name": "read_document",
  "input_schema": {
    "properties": {
      "path": { "type": "string" },
      "sheet": { "type": "string", "description": "Sheet name for XLSX (default: first)" },
      "pages": { "type": "string", "description": "Page range for PDF (e.g., '1-5')" },
      "slides": { "type": "string", "description": "Slide range for PPTX (e.g., '1-10')" }
    },
    "required": ["path"]
  }
}
```

Returns structured content:
- **PDF**: text per page, metadata (title, author, page count)
- **DOCX**: paragraphs with heading levels, tables as markdown, metadata
- **XLSX**: sheet list, cell data as markdown tables, formulas
- **PPTX**: slides with titles, body text, speaker notes, table content
- **Images**: vision-based description + any OCR text
- **Audio/Video**: transcription text + duration + metadata

#### `write_document`
Create new documents from structured content:

```json
{
  "name": "write_document",
  "input_schema": {
    "properties": {
      "path": { "type": "string" },
      "content": { "type": "string", "description": "Markdown content to convert" },
      "template": { "type": "string", "description": "Template file path (optional)" }
    },
    "required": ["path", "content"]
  }
}
```

Dispatches by extension:
- **.docx**: Convert markdown → DOCX with headings, tables, bold/italic
- **.xlsx**: Convert markdown tables → XLSX with sheets, headers, auto-width
- **.pptx**: Convert markdown sections → slides (# = slide title, body = bullet points)
- **.pdf**: Convert markdown → PDF via reportlab or weasyprint

#### `edit_document`
Targeted edits to existing documents:

```json
{
  "name": "edit_document",
  "input_schema": {
    "properties": {
      "path": { "type": "string" },
      "action": { "type": "string", "enum": ["replace_text", "add_sheet", "add_slide",
                  "update_cell", "delete_page", "set_metadata"] },
      "params": { "type": "object", "description": "Action-specific parameters" }
    },
    "required": ["path", "action", "params"]
  }
}
```

Actions:
- **DOCX**: `replace_text`, `add_paragraph`, `add_table`, `set_metadata`
- **XLSX**: `update_cell`, `add_sheet`, `add_row`, `set_formula`
- **PPTX**: `add_slide`, `update_slide`, `set_speaker_notes`
- **PDF**: `add_page`, `replace_text` (via overlay), `merge`, `split`

### 3. Knowledge Graph Integration

#### Rich Metadata Nodes

When documents are ingested, extract structural metadata for the KG:

| Format | Node metadata |
|--------|--------------|
| PDF | page_count, title, author, has_images, has_tables, file_size |
| DOCX | paragraph_count, table_count, heading_structure, word_count |
| XLSX | sheet_names, row_counts, has_formulas, has_charts |
| PPTX | slide_count, has_speaker_notes, has_images |
| Image | width, height, format, description (from vision), detected_text (OCR) |
| Audio | duration, format, sample_rate, transcription_preview |
| Video | duration, resolution, frame_count, transcription_preview |

#### New Node Types in KG

```
ingested_pdf, ingested_docx, ingested_xlsx, ingested_pptx,
ingested_image, ingested_audio, ingested_video
```

These get specific colors in the Knowledge Map visualization.

#### Cross-Format Relationships

The relationship discovery system can now find connections like:
- A PPTX presentation references data from an XLSX spreadsheet
- A PDF contract is discussed in a chat transcript
- An audio meeting transcription mentions files that exist in the project

### 4. QMD Indexing Enhancements

Currently QMD indexes only `.md` files. With this feature:

- **Ingested chunks** remain as `.md` files (existing pattern works)
- **New**: chunking is format-aware:
  - XLSX: one chunk per sheet (preserves table structure)
  - PPTX: one chunk per slide (preserves slide context)
  - Audio/Video: chunks at natural pause boundaries or fixed intervals
  - Images: single chunk with description + OCR text
- **New**: chunk frontmatter includes format-specific fields for filtering

---

## Configuration

Per-agent in `agent.json`:

```json
{
  "file_intelligence": {
    "enabled": true,
    "vision_model": "",
    "whisper_model": "base",
    "max_image_size_mb": 10,
    "max_audio_duration_min": 60,
    "max_video_duration_min": 30,
    "ocr_enabled": true,
    "transcription_language": "en"
  }
}
```

Settings UI section in agent config or global settings.

---

## Dependencies

### Required (for office documents)
```
openpyxl>=3.1,<4          # XLSX read/write
python-pptx>=0.6,<1       # PPTX read/write
python-docx>=1.0,<2       # DOCX read/write (already used)
pymupdf>=1.23,<2          # PDF read (already used)
reportlab>=4.0,<5         # PDF creation
```

### Optional (for media files)
```
faster-whisper>=1.0,<2    # Audio transcription (local, runs on CPU/GPU)
Pillow>=10.0,<11          # Image metadata extraction
ffmpeg-python>=0.2,<1     # Video audio extraction (requires ffmpeg binary)
```

### Already Available
- Vision LLM API (for image description) — uses existing provider routing
- `pymupdf` and `python-docx` — already dependencies for PDF/DOCX ingestion

---

## Implementation Plan

### Phase 1 — Office Document Parsers (~3 days)

1. **XLSX parser**: `openpyxl` → markdown tables per sheet, formulas, metadata
2. **PPTX parser**: `python-pptx` → slide text/notes/tables, metadata
3. **CSV/TSV parser**: stdlib `csv` → markdown table
4. **Extend DocumentParser.parse()** dispatch with new extensions
5. **Update IngestManager** to handle new formats

### Phase 2 — Document Tools (~2.5 days)

1. **`read_document`** tool: format-aware reader with pagination (pages/sheets/slides)
2. **`write_document`** tool: markdown → DOCX/XLSX/PPTX/PDF conversion
3. **`edit_document`** tool: targeted edits per format
4. Tool definitions, dispatch, icons, verbs

### Phase 3 — Image Intelligence (~1.5 days)

1. **Image parser**: send to vision LLM (Haiku) for description + OCR
2. **SVG parser**: extract text elements via XML
3. **Ingest pipeline**: images → description chunks in QMD
4. **`read_document`** support for image files

### Phase 4 — Audio/Video Transcription (~2 days)

1. **Audio parser**: `faster-whisper` for local transcription (or Whisper API)
2. **Video parser**: `ffmpeg` extract audio → transcribe + keyframe extraction → vision
3. **Chunking**: split transcriptions at natural boundaries
4. **`read_document`** support for audio/video files
5. **Configuration**: model size, language, duration limits

### Phase 5 — KG Integration + Settings (~1.5 days)

1. **Rich metadata** in ingested node frontmatter
2. **New node type colors** in Knowledge Map
3. **Format-specific filter chips**
4. **Settings UI**: vision model, whisper model, limits
5. **GET/POST /v1/file-intelligence/config** endpoints

**Total: ~10.5 days**

---

## Tool Workflow Examples

### Agent reads an Excel report
```
User: "Summarize the Q4 financial report in reports/q4-2025.xlsx"
Agent: [uses read_document path="reports/q4-2025.xlsx"]
       → Gets: 3 sheets (Revenue, Costs, Summary), markdown tables
       → Summarizes the data
```

### Agent creates a presentation from notes
```
User: "Create a presentation from my meeting notes"
Agent: [uses memory_recall query="meeting notes"]
       → Gets relevant notes
       [uses write_document path="meeting-recap.pptx" content="# Q4 Review\n- Revenue up 15%\n..."]
       → Creates PPTX with slides
```

### Agent ingests a screenshot
```
User: [uploads screenshot.png]
Agent: [image sent to vision LLM]
       → "This is a screenshot of an error dialog showing 'Connection refused on port 8420'"
       [auto-ingested into QMD with description]
       → Searchable via memory_recall
```

### Agent transcribes a meeting
```
User: "Transcribe and summarize this meeting recording"
Agent: [uses read_document path="meeting-2026-03-23.mp3"]
       → Gets: transcription text (23 minutes, 4500 words)
       → Summarizes key decisions and action items
```

---

## Benefits

- **Full-spectrum file understanding** — agents can read any common file format
- **Create deliverables** — agents can produce DOCX reports, XLSX data, PPTX presentations
- **Rich KG** — documents have structural metadata, not just text blobs
- **Searchable media** — images, audio, video transcriptions indexed in QMD
- **Zero manual effort** — watched folders auto-ingest new files of any type
- **Configurable** — vision model, whisper model, size limits all adjustable

## Risks

- **Large dependencies** — whisper models are 100MB-1.5GB. Mitigated: optional install, configurable model size
- **Vision API costs** — each image description costs ~$0.01 with Haiku. Mitigated: configurable, only on ingest
- **Transcription quality** — Whisper works well for clear speech but struggles with accents/noise. Mitigated: language config
- **PDF editing limitations** — PDFs are not designed for editing. Overlay-based edits only, no true content modification
- **ffmpeg dependency** — must be installed system-wide for video. Mitigated: graceful fallback if missing
