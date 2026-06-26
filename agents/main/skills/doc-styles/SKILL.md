---
name: Document Styles
description: Editable per-format style presets (fonts, colors, layout) that write_document and render_diagram apply DETERMINISTICALLY when creating .docx/.pptx/.pdf files and diagrams. Not interpreted by the model — Python reads the YAML and sets the styling, so output looks consistent regardless of model strength.
metadata:
  type: doc-style
  version: 1.6.0
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
  (heading/body/accent/table_header_*), `docx` (table_style/heading_bold +
  **polish keys** `zebra_fill`, `rule_color`, `strip_emoji`, `risk_badges`,
  `cover`, `toc` — see below), `pdf` (page_size letter|a4, margin_inch), `pptx`
  (title_color/body_color/accent/background), `mermaid` (theme default|dark|
  forest|neutral, background).
- **Header / footer / logo** (running page chrome):
  - `header` / `footer`: `text` (supports `{page}` / `{date}` tokens), `align`
    (left|center|right), `font_size` (pt), `color`. `footer.text` is for MANUAL
    footer content only — the classification, AI-disclosure and page-number lines
    are added automatically (see below), so don't hand-type them into `text`.
  - `footer.page_numbers: true` → a live page number on **its own footer line**,
    formatted `Seite - N`, in the same font/size/colour as the footer text
    (v9.208.0; previously it trailed the footer text inline as `Seite N`).
  - `footer.classification: true` (default) → an **automatic, content-derived**
    sensitivity line `Klassifizierung: <Stufe>` (Öffentlich / Intern / Vertraulich
    / Streng vertraulich), on its own line. The level comes from the ARL content
    heuristic (`engine/classification.py`, regex + keyword + PII, **no LLM**) run
    over the document's own text; a business report floors at **Intern** (never
    auto-labelled Öffentlich). Set `false` to omit.
  - `footer.ai_disclosure: true` (default) → an **automatic** EU-AI-Act
    transparency line ("Dieses Dokument wurde mit Unterstützung künstlicher
    Intelligenz erstellt."), on its own line. Override the wording with
    `footer.ai_disclosure_text`, or set `ai_disclosure: false` to omit.
  - Footer line order (each its own line): manual `text` → classification →
    AI-disclosure → page number.
  - `logo`: `file` (basename of an image next to the preset — upload it in the
    GUI editor), `width_inch`, `align`, `position` (header|footer|slide|none). A
    header logo automatically pushes the top margin + header distance down so it
    never overlaps the first heading (v9.208.0).
- Unknown keys are ignored; missing keys fall back to built-in defaults.

## How it reaches the file
- **docx**: Normal + Heading 1-3 styles get the fonts/sizes/colors; tables get
  `docx.table_style`; every section's header/footer gets the running text
  (`{page}` → live Word PAGE field) + logo picture.
  **Automatic polish (deterministic, every model)** — keys under `docx`:
  - `cover: true` → a title page from the FIRST `# H1` + leading `Key: value`
    frontmatter lines, rendered only for substantial reports (≥4 headings, OR an
    H1+frontmatter block, OR ≥30 non-blank lines — a short memo stays one page).
  - `toc: true` → a **pre-populated, page-numbered** table of contents after the
    cover: each H1–H3 becomes a real entry (internal hyperlink + `PAGEREF` page
    number) wrapped in a live Word `TOC` field, and `settings.xml` requests a
    field recalc on open — so a filled ToC is visible immediately (no "press F9"
    placeholder) and F9 only re-flows page numbers. The SAME `cover`/`toc` keys
    drive the **.pdf** cover page + a clickable, page-numbered ToC (reportlab
    2-pass build). A **Versionshistorie / Änderungshistorie** section (see below)
    is deliberately EXCLUDED from the ToC.
  - **Page-break hygiene (deterministic, every doc)**: headings get `keepNext`/
    `keepLines` (a heading never strands alone at a page bottom — no Schusterjunge);
    every table row gets `cantSplit` (a row never breaks mid-row, killing the
    "one line on the old page, the rest on the next" artefact), and the header row
    repeats atop every page a long table spans.
  - **Table column widths are content-proportional** (docx + pdf): each column is
    sized by its longest cell with a per-column floor, so a narrow-header column
    (e.g. "Nr") no longer collapses to a sliver that wraps every word.
  - **Version history**: write a final section headed `## Versionshistorie` (or
    `## Änderungshistorie`) containing a normal markdown table — recommended
    columns `Version | Datum | Autor | Änderung`. The renderer starts it on a
    fresh page (table stays whole) and keeps it out of the ToC. There is NO
    special syntax — it's a plain markdown table the model writes; just use one of
    the recognised headings so the page-break + ToC-exclusion fire. **One row per
    version** — bundle ALL edits made within a single turn into ONE new version
    row (don't add a row per `write_document` call); bump the version number once
    per turn, at the end.
  - `zebra_fill` (#hex) → alternating body-row shading; table headers get
    `colors.table_header_bg`/`_text` (dark fill + white text).
  - `rule_color` (#hex) → `---` lines become real divider rules; H1 gets an
    underline.
  - `strip_emoji: true` → a leading emoji on a heading is removed (regulatory
    docs stay clean); inline `**bold**`/`*italic*` is parsed in headings AND
    table cells (not left verbatim).
  - `risk_badges: true` → a table column of risk ratings (the column whose cells
    most often read gering/mittel/erhöht/hoch) is colour-badged green/amber/red.
  - **`::kpi VALUE | LABEL | risk`** lines (in the markdown `content`, not the
    preset) render a coloured KPI stat-box strip — the one polish feature the
    model triggers explicitly.
- **pdf**: page size + margins + heading/body colors (reportlab); header/footer
  text + page number + logo drawn on every page via an onPage canvas callback.
  Gets the SAME automatic polish as docx — cover page, clickable page-numbered
  table-of-contents, dark table headers + zebra rows, `---` dividers, emoji-strip
  + inline-markdown in headings, colour-coded risk badges, and `::kpi` stat boxes.
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
