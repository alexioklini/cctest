---
name: Document Styles
description: Editable per-format style presets (fonts, colors, layout) that write_document and render_diagram apply DETERMINISTICALLY when creating .docx/.pptx/.pdf files and diagrams. Not interpreted by the model — Python reads the YAML and sets the styling, so output looks consistent regardless of model strength.
metadata:
  type: doc-style
  version: 1.0.0
---

# Document Styles

Each `<name>.yaml` in this folder is a **style preset**. When a document is
created with `write_document(..., style="<name>")` (or a diagram with
`render_diagram(..., style="<name>")`), the tool loads that preset and applies
its fonts / colors / layout in code. The model only writes plain markdown and
names the preset — it never has to reason about styling (keeps weaker models
like Mistral reliable).

## Editing / adding a preset
- Copy `corporate.yaml`, rename, edit the values (hex colors `#RRGGBB`, sizes in
  points, font names must be installed/available).
- Keys: `fonts` (body/heading/mono), `sizes` (body/h1/h2/h3), `colors`
  (heading/body/accent/table_header_*), `docx` (table_style/heading_bold),
  `pdf` (page_size letter|a4, margin_inch), `pptx` (title_color/body_color/
  accent/background), `mermaid` (theme default|dark|forest|neutral, background).
- Unknown keys are ignored; missing keys fall back to built-in defaults.

## How it reaches the file
- **docx**: Normal + Heading 1-3 styles get the fonts/sizes/colors; tables get
  `docx.table_style`.
- **pdf**: page size + margins + heading/body colors (reportlab).
- **pptx**: title/body run colors per slide.
- **diagrams**: `render_diagram(style=…)` inherits `mermaid.theme/background` so
  charts match the document they're embedded in.

`corporate.yaml` is the starter preset (navy headings, Calibri, neutral mermaid).
