---
name: Document Styles
description: Editable per-format style presets (fonts, colors, layout) that write_document and render_diagram apply DETERMINISTICALLY when creating .docx/.pptx/.pdf files and diagrams. Not interpreted by the model — Python reads the YAML and sets the styling, so output looks consistent regardless of model strength.
metadata:
  type: doc-style
  version: 1.3.0
---

# Document Styles

Each `<name>.yaml` in this folder is a **style preset**. When a document is
created with `write_document(..., style="<name>")` — `.docx` / `.pptx` / `.pdf` /
**`.html`** — (or a diagram with `render_diagram(..., style="<name>")`), the tool
loads that preset and applies its fonts / colors / layout in code. The model only writes plain markdown and
names the preset — it never has to reason about styling (keeps weaker models
like Mistral reliable).

**A default preset is applied even when the model OMITS `style=`** (v9.154.0):
resolution is explicit `style=` → the project's `doc_style` (project.json) →
`config.json doc_styles.default` → `corporate` (if present) → built-in look. So
a report is on-brand by default; the model should still write PLAIN MARKDOWN (not
hand-rolled CSS) so the preset can style it. (For `.html`, raw HTML the model
passes is kept as-is and only gains the header/footer/logo; markdown gets the
full preset — so markdown + a named style is the most consistent.)

## Editing / adding a preset
- Copy `corporate.yaml`, rename, edit the values (hex colors `#RRGGBB`, sizes in
  points, font names must be installed/available).
- Keys: `fonts` (body/heading/mono), `sizes` (body/h1/h2/h3), `colors`
  (heading/body/accent/table_header_*), `docx` (table_style/heading_bold),
  `pdf` (page_size letter|a4, margin_inch), `pptx` (title_color/body_color/
  accent/background), `mermaid` (theme default|dark|forest|neutral, background).
- **Header / footer / logo** (running page chrome):
  - `header` / `footer`: `text` (supports `{page}` / `{date}` tokens), `align`
    (left|center|right), `font_size` (pt), `color`. `footer.page_numbers: true`
    appends a live page number.
  - `logo`: `file` (basename of an image next to the preset — upload it in the
    GUI editor), `width_inch`, `align`, `position` (header|footer|slide|none).
- Unknown keys are ignored; missing keys fall back to built-in defaults.

## How it reaches the file
- **docx**: Normal + Heading 1-3 styles get the fonts/sizes/colors; tables get
  `docx.table_style`; every section's header/footer gets the running text
  (`{page}` → live Word PAGE field) + logo picture.
- **pdf**: page size + margins + heading/body colors (reportlab); header/footer
  text + page number + logo drawn on every page via an onPage canvas callback.
- **html**: a self-contained styled document. `content` may be **markdown**
  (rendered with the full preset — body/heading fonts+sizes+colors, accent links,
  table header colors) OR **raw HTML** (a full `<html>` doc is auto-detected and
  written through as-is, NOT escaped; its own CSS is kept). Either way local
  `<img src="file">` / `![](file)` images are inlined as base64 and the preset's
  header/footer bands + logo are added ({date} resolves; {page} via a print-only
  CSS counter). Always create HTML reports with `write_document` (not raw
  `write_file`) so the preset chrome is applied.
- **pptx**: title/body run colors per slide; the logo (any non-`none`
  `position`) is placed as a corner image and the footer text + page band is
  drawn on every slide.
- **diagrams**: `render_diagram(style=…)` inherits `mermaid.theme/background` so
  charts match the document they're embedded in.

`corporate.yaml` is the starter preset (navy headings, Calibri, neutral mermaid).
