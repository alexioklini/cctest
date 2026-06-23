---
name: Document Markdown
description: How to write markdown for write_document so it converts to a polished .docx / .pdf. The converter (markdown-it-py → our renderer) understands standard markdown PLUS two house conventions (::kpi stat boxes, a Bewertung/Risiko column for auto risk-badges). Read this before producing a report/letter/analysis as a Word or PDF file.
metadata:
  type: guide
  version: 1.1.0
---

# Document Markdown

`write_document(path, content, style?)` turns the **plain markdown** you write
into a styled `.docx` / `.pdf` / `.pptx` / `.html`. You never write HTML, OOXML
or hand-rolled styling — write clean markdown and the converter applies the
preset (fonts/colours/layout) deterministically. This guide is how to write that
markdown so it converts beautifully.

The converter parses your text with a real CommonMark parser (markdown-it), so
**all standard markdown works** — and two extra house conventions unlock the
polished look. Follow these and even a short report comes out on-brand.

## Golden rules

1. **Write PLAIN markdown, never HTML or CSS.** `## Heading`, not `<h2>`. The
   preset styles it; ad-hoc HTML defeats that.
2. **No `**bold**` markers or emoji IN headings.** Write `## Management Summary`,
   not `## **📊 Management Summary**`. (Leading emoji is stripped and `**` inside a
   heading is handled, but clean headings are best.) Bold/italic in *body* text
   and table cells is fine and renders correctly.
3. **One blank line between blocks** (heading, paragraph, list, table, code).
   Markdown needs the blank line to separate them — without it a heading can glue
   to the next paragraph.
4. **Use real markdown for structure**, it now all renders natively:
   - Bullet list: `- item` (nest with two-space indent → `  - sub-item`)
   - Numbered list: `1. item`
   - Quote: `> quoted text`
   - Code: triple-backtick fenced block (with a language tag if relevant)
   - Link: `[text](https://url)` → a real clickable link
   - Table: standard `| a | b |` with a `|---|---|` separator row
   - Divider: a line of `---`
   - Image/diagram: `![alt](file.png)` (render_diagram first, embed the file)

## House convention 1 — KPI stat boxes

To highlight 2–4 headline metrics as a row of coloured stat boxes, put
consecutive `::kpi` lines (one per box), each `VALUE | LABEL | risk`:

```
::kpi 1,55 | Inhärentes Risiko | mittel
::kpi 1,12 | Kontrollumfeld | sehr gut
::kpi 1,34 | Residualrisiko | gering
```

The **third field** colours the box on the risk scale (see below). Put the block
right after the title or at the top of the summary. Use it for the few numbers
that matter most — not for every figure.

## House convention 2 — auto risk-badges in tables

If a table has a column of risk ratings, **name it `Bewertung`, `Risiko`,
`Rating`, `Einstufung` or `Stufe`** and fill its cells with risk words. The
converter auto-colours those cells green / amber / red — you write nothing extra:

```
| Risikofaktor | Gewichtung | Bewertung | Begründung |
|---|---|---|---|
| Kundenrisiko | 25 % | Erhöht | … |
| Länderrisiko | 20 % | Mittel | … |
| Technologie  | 10 % | Gering | … |
```

**Risk scale (the words that colour KPI boxes and badges):**
- `gering` / `niedrig` / `sehr gut` → green
- `mittel` / `angemessen` / `moderat` → amber
- `erhöht` → orange-red
- `hoch` → red

Write the rating word plainly in the cell (`Erhöht`, not `**Erhöht** 🔴`).

## Cover page & table of contents (automatic)

For a **substantial report** (≥4 headings, or a title with `Key: value` lines
under it, or many lines) the converter renders a cover page + a table of
contents automatically. To get a good cover, start the document with a single
`# Title` H1 followed immediately by frontmatter lines:

```
# Risikoanalyse Q4 2025
Stichtag: 31.12.2025
Verantwortlich: …
Freigabe: …

## Management Summary
…
```

The title + those `Key: value` lines become the cover; everything else flows
into the body. A short memo (few headings, no frontmatter) stays single-page —
don't force a cover with filler.

## Slides (.pptx)

For a `.pptx` deck, a `# ` or `## ` heading starts a **new slide** (its text
becomes the slide title); deeper `### ` headings stay in the slide body. Each
slide's body is the same rich markdown — bullets (nested for sub-points), tables
(with the Bewertung/Risiko auto-badges), `**bold**`/`*italic*`/links all render
as real formatting. A slide whose body is only an `![alt](file.png)` becomes a
full-bleed picture slide. Keep slide bodies to a few bullets — slides are for
key points, not paragraphs.

```
# Deck-Titel

## Erste Folie
- Kernpunkt mit **Betonung**
- Zweiter Punkt
  - Unterpunkt

## Datenfolie
| Faktor | Bewertung |
|---|---|
| A | Hoch |
```

## Quick template — a clean report

```
# Berichtstitel
Stichtag: …
Verantwortlich: …

::kpi 1,3 | Gesamtrisiko | gering

## Management Summary
Fließtext. **Wichtiges** fett, *Nuancen* kursiv.

Kernpunkte:

- Erster Punkt
- Zweiter Punkt

## Analyse
| Faktor | Gewichtung | Bewertung | Begründung |
|---|---|---|---|
| … | … | Mittel | … |

## Methodik
> Zitat aus der Quelle, falls nötig.

## Fazit
Schluss.
```

That's it — plain markdown in, polished document out. Pair with a `style=`
preset (or `style='reference'` to match an attached template) for the
fonts/colours/logo. See the **Document Styles** skill for the preset side.
