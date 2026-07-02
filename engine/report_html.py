# Report HTML renderer — turn a generated markdown report into a self-contained,
# editorial-quality HTML page. Shared by the Studio output pipeline + Deep Research
# (both save through engine.output_gen.save_report_output), so every report the
# user receives can be offered as a polished, downloadable HTML document.
#
# The visual language deliberately MIMICS Odysseus's deep-research reports
# (src/visual_report.py, AGPL — we reimplement the look, not the code): a warm
# editorial palette (cream + terracotta + gold), a serif display / sans body
# split, a slowly-drifting "aurora" gradient background with SVG film-grain, a
# drop-cap opening paragraph, gradient-underlined section headings, a gold italic
# blockquote, a sticky table-of-contents sidebar, and a collapsible sources panel.
# Light + dark via prefers-color-scheme; print-ready.
#
# DESIGN:
# - ZERO new dependencies. The main server runs on bare Homebrew python3 (no
#   `markdown`/`nh3`/`bleach`), so this module carries a small, focused
#   markdown→HTML converter covering exactly the subset our reports emit
#   (## / ### headings, **bold**, *italic*, `code`, [links](url), bullet +
#   numbered lists, > blockquotes, --- rules, pipe tables, fenced code).
# - SELF-CONTAINED: system/serif font stacks (no remote font CDN), all CSS
#   inlined, the only JS is a tiny scrollspy for the TOC + a copy/print toolbar.
#   The file opens offline or as an email attachment and still looks right.
# - SAFE BY CONSTRUCTION: input is our own model output (not arbitrary user
#   HTML). Every text node is HTML-escaped before tags are emitted, and raw HTML
#   never passes through — so no <script>/onclick/javascript: can survive.
#
# Single public entrypoint: render_report_html(markdown, title, meta, sources,
# category, stats) -> str (a complete <!doctype html> document).

import base64
import contextvars
import html
import mimetypes
import os
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

# The base directory a report is written into — set per render so relative image
# paths in `![alt](chart.png)` can be resolved + inlined. contextvar so nested/
# concurrent renders don't clobber each other.
_render_doc_dir: contextvars.ContextVar[str] = contextvars.ContextVar("_render_doc_dir", default="")


def _local_image_data_uri(src: str) -> Optional[str]:
    """Resolve a relative/absolute local image path (against the current render's
    doc dir) and return a base64 data-URI, or None if it can't be read. Keeps the
    report self-contained — same intent as the docx/pdf image embedding."""
    src = (src or "").strip()
    if not src or re.match(r"^(https?:|data:|javascript:)", src, re.I):
        return None
    candidates = []
    if os.path.isabs(src):
        candidates.append(src)
    else:
        base = _render_doc_dir.get() or "."
        candidates.append(os.path.join(base, src))
        candidates.append(src)
    for p in candidates:
        try:
            if os.path.isfile(p) and os.path.getsize(p) <= 8 * 1024 * 1024:
                ctype = mimetypes.guess_type(p)[0] or "image/png"
                if not ctype.startswith("image/"):
                    return None
                with open(p, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                return f"data:{ctype};base64,{b64}"
        except OSError:
            continue
    return None


# ---------------------------------------------------------------------------
# Inline citations — [Quelle: <source> — "<quote>"] → numbered chip + legend
# ---------------------------------------------------------------------------
# The report model cites sources inline with the same `[Quelle: file — "quote"]`
# convention the chat view renders as compact pins (web/js/chat_render.js). Left
# raw, these long brackets clutter the prose. We pre-extract them into a numbered
# chip [n] at the citation point + a "Belege" legend at the foot — a Python port
# of the chat view's extractCitationsFromRaw / parseCitationBodyRaw / pin render.
#
# A sentinel token replaces each bracket BEFORE block/inline parsing so the quote
# (which may contain markdown, pipes, quotes) never confuses the markdown pass;
# _inline swaps the sentinel for the chip HTML. The per-render citation list rides
# a contextvar (single render per call).
_CITE_SENTINEL = "⁣CIT⁣{}⁣/CIT⁣"   # U+2063 invisible separator
_CITE_SENTINEL_RE = re.compile("⁣CIT⁣(\\d+)⁣/CIT⁣")
_render_citations: contextvars.ContextVar[list] = contextvars.ContextVar("_render_citations", default=None)

# Citation bracket: either a "Quelle:/source:"-prefixed body, or a no-prefix
# "<file> — "quote"" (the em-dash + quote guards against matching plain links).
_CITE_BODY = (r"(?:(?:Quelle|QUELLE|source|Source|SOURCE):\s*(?:[^\[\]]|\[\.{2,3}\])+?"
              r"|[^\[\]\n]+?\s*[—–]\s*[„\"“«][^„\"“”»\]]+[„\"“”»][^\[\]]*?)")
_CITE_RE = re.compile(r"\[(" + _CITE_BODY + r")\]")
_CITE_BACKTICK_RE = re.compile(
    r"`(\[[^\]]*?(?:(?:Quelle|QUELLE|source|Source|SOURCE):|[—–][^\"]*[„\"“])[^\]]*?\])`")


def _parse_citation_body(body: str) -> Optional[dict]:
    """'<source> — "<quote>"' (optional 'Quelle:' prefix) → {file, locator, quote}.
    Mirrors chat_render.parseCitationBodyRaw."""
    if not body:
        return None
    s = body.strip()
    s = re.sub(r'^(?:Quelle|QUELLE|source|Source|SOURCE):\s*', '', s, flags=re.I)
    quote = ""
    qm = re.search(r'\*\s*[„"“]([^„"“”]+)[“"”]\s*\*\s*$', s)
    if not qm:
        qm = re.search(r'[„"“]([^„"“”]+)[“"”]\s*$', s)
    if qm:
        quote = qm.group(1).strip()
        s = s[:qm.start()].strip()
        s = re.sub(r'\s*[—–-]\s*$', '', s).strip()
    file = s
    locator = ""
    lm = re.search(r'\s+(Page\s+\S+|Slide\s+\S+|Sheet\s+["“„][^"“”]+["“”]|§\s*\S+.*|Zeile[n]?\s*\d+[\d\s\-–]*)$', s)
    if lm:
        locator = lm.group(1).strip()
        file = s[:lm.start()].strip()
    if not file:
        file = body.strip()
    file = re.sub(r'\.(pdf|docx|pptx|xlsx|xlsm|eml|msg)\.md$', r'.\1', file, flags=re.I)
    return {"file": file, "locator": locator, "quote": quote}


def _extract_citations(md: str) -> tuple[str, list]:
    """Replace every citation bracket in the markdown with a sentinel + collect the
    parsed citations. Standalone bracket lines are first pulled up onto the prior
    line so the chip renders at the end of the claim, not as its own paragraph."""
    text = _CITE_BACKTICK_RE.sub(r"\1", md or "")
    # Pull a standalone bracket up onto the previous non-blank line (repeat so a
    # run of bracket-paragraphs collapses one by one). Skip table rows.
    pull = re.compile(r'([^\n])[ \t]*\n(?:[ \t]*\n)*[ \t]*(\[' + _CITE_BODY + r'\])')

    def _join(m):
        prev_line_start = text.rfind("\n", 0, m.start(2))
        # crude table guard: if the char before the bracket run ends a table cell
        return m.group(1) + " " + m.group(2)

    prev = None
    while prev != text:
        prev = text
        text = pull.sub(_join, text)

    citations: list = []

    def _repl(m):
        parsed = _parse_citation_body(m.group(1))
        if not parsed:
            return m.group(0)
        idx = len(citations)
        citations.append(parsed)
        return _CITE_SENTINEL.format(idx)

    stripped = _CITE_RE.sub(_repl, text)
    return stripped, citations


def _citation_chip(c: dict, n: int) -> str:
    """A numbered superscript chip [n] with the source+quote in its tooltip."""
    tip = c["file"]
    if c.get("locator"):
        tip += " · " + c["locator"]
    if c.get("quote"):
        tip += f'\n\n"{c["quote"]}"'
    return (f'<a href="#cite-{n}" class="cite-chip" title="{html.escape(tip, quote=True)}">'
            f'<sup>[{n}]</sup></a>')


def _citations_legend_html(citations: list) -> str:
    """The 'Belege' footer legend: [n] → source — "quote", one row per citation."""
    if not citations:
        return ""
    rows = []
    for i, c in enumerate(citations):
        n = i + 1
        src = html.escape(c["file"])
        loc = f' · {html.escape(c["locator"])}' if c.get("locator") else ""
        quote = (f'<span class="cite-quote">„{html.escape(c["quote"])}"</span>'
                 if c.get("quote") else "")
        rows.append(f'<li id="cite-{n}"><span class="cite-n">[{n}]</span> '
                    f'<span class="cite-src">{src}{loc}</span>{quote}</li>')
    return ('<section class="citations-panel"><details open>'
            f'<summary>Belege ({len(citations)})</summary>'
            f'<ol class="citations-list">{"".join(rows)}</ol>'
            '</details></section>')


# Category → hero eyebrow label (German, to match the UI). The palette stays the
# warm editorial one across categories — the label is what tells a product
# report from a fact-check. Keys match engine.deep_research category codes.
_CATEGORY_LABEL = {
    "product":    "Produkt-Recherche",
    "comparison": "Vergleich",
    "howto":      "Anleitung",
    "factcheck":  "Faktencheck",
    "report":     "Recherchebericht",
    "studio":     "Dokument",
}


# ---------------------------------------------------------------------------
# Minimal, focused markdown → HTML (covers our report subset only)
# ---------------------------------------------------------------------------

_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*([^*\n]+)\*(?![*\w])")
_STRIKE = re.compile(r"~~([^~]+)~~")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


# ::kpi VALUE | LABEL | risk → a coloured stat box. Same convention the docx/pdf
# report path understands (file_tools._kpi_match / _emit_kpi_strip); ported here so
# the editorial HTML renders the strip instead of showing the raw '::kpi …' line.
# Models are inconsistent: they write one ::kpi per line OR several ::kpi on ONE
# line, and a '#'/'##' marker may lead. So detection is "line CONTAINS ::kpi" and
# a whole block is split on every ::kpi marker (not anchored to line-start).
_KPI_HAS = re.compile(r"::kpi\b", re.IGNORECASE)
_KPI_SPLIT = re.compile(r"::kpi\b", re.IGNORECASE)

# risk keyword → (foreground hex, background hex). Mirrors file_tools._RISK_BADGES.
_KPI_RISK_COLORS = [
    (("sehr gut", "gering", "niedrig", "low"), ("#548235", "#e2efda")),
    (("erhöht", "erhoht", "elevated"),         ("#c55a11", "#fce4d6")),
    (("hoch", "high", "hohes"),                ("#c00000", "#f8d7da")),
    (("mittel", "angemessen", "medium", "moderat"), ("#bf8f00", "#fff2cc")),
]


def _kpi_line(line: str) -> bool:
    """True if a line contains at least one ::kpi marker (heading-prefix tolerated)."""
    return bool(_KPI_HAS.search(line or ""))


def _kpi_field(s: str) -> str:
    """Trim a KPI field: strip whitespace, stray '#'/'*' emphasis, wrapping quotes."""
    s = (s or "").strip().strip("#").strip()
    s = s.strip("*").strip()
    if len(s) >= 2 and s[0] in "\"'" and s[-1] == s[0]:
        s = s[1:-1].strip()
    return s


def _parse_kpis(blob: str) -> list:
    """Split a text blob on every ::kpi marker → [(value, label, badge)]. Each
    record splits on '|' but caps at 3 fields, so a pipe INSIDE the third field
    (e.g. a quoted 'Turnover|10,377,747') is preserved, not mis-split."""
    kpis = []
    for chunk in _KPI_SPLIT.split(blob):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split("|", 2)  # at most value | label | rest
        value = _kpi_field(parts[0]) if parts else ""
        label = _kpi_field(parts[1]) if len(parts) > 1 else ""
        badge = _kpi_field(parts[2]) if len(parts) > 2 else (label or value)
        if not value and not label:
            continue
        kpis.append((value, label, badge))
    return kpis


def _kpi_colors(badge: str):
    """badge word → (fg, bg) hex, or a neutral default if nothing matches."""
    v = (badge or "").strip().lower()
    for keys, cols in _KPI_RISK_COLORS:
        if any(k in v for k in keys):
            return cols
    return ("#44546a", "#edf1f8")


def _kpi_strip_html(kpis: list) -> str:
    """Render collected ::kpi tuples as a row of coloured stat boxes."""
    boxes = []
    for value, label, badge in kpis:
        fg, bg = _kpi_colors(badge)
        cap = (f'<span class="kpi-label">{html.escape(label.upper())}</span>'
               if label else "")
        boxes.append(
            f'<div class="kpi-box" style="--kpi-fg:{fg};--kpi-bg:{bg}">'
            f'<span class="kpi-value">{_inline(value)}</span>{cap}</div>')
    return f'<div class="kpi-strip">{"".join(boxes)}</div>'


def _strip_inline_md(text: str) -> str:
    """Strip inline markdown markers to plain text — for places that can't carry
    tags (the <title>, the hero <h1>, TOC/anchor text). Without this a model that
    writes '# **Titel**' leaves the ** visible in the headline."""
    t = text or ""
    t = _INLINE_CODE.sub(r"\1", t)
    t = _BOLD.sub(r"\1", t)
    t = _ITALIC.sub(r"\1", t)
    t = _LINK.sub(r"\1", t)
    # Any leftover stray emphasis markers (unbalanced ** / *) → drop.
    t = t.replace("**", "").strip()
    return t


def _slug(text: str) -> str:
    """Stable heading anchor id from heading text."""
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    return re.sub(r"[\s_]+", "-", s) or "section"


def _inline(text: str) -> str:
    """Render inline markdown in a single text run. Escapes first, then re-adds
    only the formatting tags we emit — so the source text can never inject HTML.
    [], (), and / survive html.escape, so the link syntax still matches."""
    out = html.escape(text, quote=False)
    out = _INLINE_CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _BOLD.sub(lambda m: f"<strong>{m.group(1)}</strong>", out)
    out = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", out)
    out = _STRIKE.sub(lambda m: f"<del>{m.group(1)}</del>", out)

    def _image(m):
        # Images BEFORE links (![alt](url) starts with the link pattern). http(s)
        # src is embedded directly; a LOCAL path (e.g. a render_diagram PNG) is
        # read + inlined as a base64 data-URI so the report stays self-contained
        # (parity with the docx/pdf path). Unresolvable → drop to alt text.
        alt, url, cap = m.group(1), m.group(2), m.group(3) or ""
        if re.match(r"^https?://", url, re.I):
            safe_src = html.escape(url, quote=True)
        else:
            data_uri = _local_image_data_uri(url)
            if not data_uri:
                return html.escape(m.group(0), quote=False)
            safe_src = html.escape(data_uri, quote=True)
        safe_alt = html.escape(alt, quote=True)
        caption = (f'<figcaption>{html.escape(cap)}</figcaption>') if cap else ""
        return (f'<figure class="report-figure"><img src="{safe_src}" alt="{safe_alt}" '
                f'loading="lazy" referrerpolicy="no-referrer">{caption}</figure>')

    out = _IMAGE.sub(_image, out)

    def _link(m):
        label, url = m.group(1), m.group(2)
        # Only http(s)/mailto become anchors; anything else stays plain text
        # (defends against javascript:/data: even though input is our own).
        if not re.match(r"^(https?:|mailto:)", url, re.I):
            return html.escape(m.group(0), quote=False)
        safe_url = html.escape(url, quote=True)
        return (f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
                f"{label}</a>")

    out = _LINK.sub(_link, out)

    # Citation sentinels (invisible U+2063 tokens, survive html.escape) → chips.
    cites = _render_citations.get()
    if cites is not None and "⁣CIT⁣" in out:
        out = _CITE_SENTINEL_RE.sub(
            lambda m: (_citation_chip(cites[int(m.group(1))], int(m.group(1)) + 1)
                       if int(m.group(1)) < len(cites) else ""), out)
    return out


# A list item: (leading indent)(marker)(space)(content). Marker = -/*/+ or `1.`.
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")


def _block_starts(line: str) -> bool:
    """True if a line opens a block that must NOT be swallowed as a lazy
    continuation (heading / fence / hr / another quote / a list item)."""
    s = line.strip()
    return (s.startswith("#") or s.startswith("```") or s.startswith(">")
            or bool(re.match(r"^([-*_])\1{2,}$", s)) or bool(_LIST_ITEM.match(line)))


def _render_list(lines: list[str], i: int, n: int) -> tuple[str, int]:
    """Render a (possibly nested) list starting at line `i`. An item indented
    deeper than this list's markers starts a sub-list (rendered recursively and
    nested inside the preceding <li>); a dedent ends this list. Returns
    (html, next_index)."""
    m0 = _LIST_ITEM.match(lines[i])
    base_indent = len(m0.group(1))
    ordered = bool(re.match(r"^\d+[.)]$", m0.group(2)))
    tag = "ol" if ordered else "ul"
    items: list[str] = []          # each entry = inner HTML of one <li>
    while i < n:
        if not lines[i].strip():   # blank line: tolerate (loose list), peek ahead
            j = i + 1
            while j < n and not lines[j].strip():
                j += 1
            nxt = _LIST_ITEM.match(lines[j]) if j < n else None
            if nxt and len(nxt.group(1)) >= base_indent:
                i = j
                continue
            break
        m = _LIST_ITEM.match(lines[i])
        if not m:
            break                  # non-list content ends the list
        indent = len(m.group(1))
        if indent < base_indent:
            break                  # dedent → belongs to an outer list
        if indent > base_indent:   # deeper → sub-list, nest into the last item
            sub, i = _render_list(lines, i, n)
            if items:
                items[-1] += sub
            else:
                items.append(sub)
            continue
        items.append(_inline(m.group(3).strip()))
        i += 1
    body = "".join(f"<li>{it}</li>" for it in items)
    return f"<{tag}>{body}</{tag}>", i


def _md_to_html(md: str) -> tuple[str, list[tuple[int, str, str]]]:
    """Convert our report-subset markdown to an HTML body fragment.

    Returns (html_body, toc) where toc = [(level, anchor_id, heading_text)] for
    ## and ### headings, used to build the table of contents."""
    lines = (md or "").replace("\r\n", "\n").split("\n")
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    i, n = 0, len(lines)

    para: list[str] = []

    def flush_para():
        if para:
            out.append(f"<p>{_inline(' '.join(para).strip())}</p>")
            para.clear()

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # Fenced code block (with language detection for mermaid / chart embeds)
        if stripped.startswith("```"):
            flush_para()
            lang = stripped[3:].strip().lower()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence
            code_text = "\n".join(code)
            # ```mermaid → render to inline SVG (reuses the write_document pipeline).
            if lang in ("mermaid", "mmd"):
                fig = _render_mermaid_inline(code_text)
                if fig:
                    out.append(fig)
                    continue
            # ```chart → render a data chart from the JSON spec to inline SVG.
            if lang == "chart":
                fig = _render_chart_inline(code_text)
                if fig:
                    out.append(fig)
                    continue
            body = html.escape(code_text, quote=False)
            out.append(f"<pre><code>{body}</code></pre>")
            continue

        # ::kpi stat boxes. Collect every line that CONTAINS a ::kpi marker (blank
        # lines between them tolerated), join them, and split the whole blob on the
        # markers — so it works whether the model wrote one ::kpi per line OR packed
        # several onto a single line. Checked BEFORE headings so a '## ::kpi …' line
        # becomes boxes, not a heading.
        if _kpi_line(line):
            flush_para()
            blob_lines = []
            while i < n:
                if _kpi_line(lines[i]):
                    blob_lines.append(lines[i])
                    i += 1
                elif not lines[i].strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and _kpi_line(lines[j]):
                        i = j
                    else:
                        break
                else:
                    break
            kpis = _parse_kpis(" ".join(blob_lines))
            if kpis:
                out.append(_kpi_strip_html(kpis))
            continue

        # Horizontal rule
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            flush_para()
            out.append("<hr>")
            i += 1
            continue

        # Headings (#..####)
        m = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if m:
            flush_para()
            level = len(m.group(1))
            text = m.group(2).strip()
            anchor = _slug(text)
            if level in (2, 3):
                toc.append((level, anchor, text))
            out.append(f'<h{level} id="{anchor}">{_inline(text)}</h{level}>')
            i += 1
            continue

        # Blockquote — collect the quoted region, strip one '>' level, then
        # RECURSE so its inner markdown (lists, nested quotes, paragraphs) renders
        # as real blocks instead of flattened text.
        if stripped.startswith(">"):
            flush_para()
            quote: list[str] = []
            while i < n and (lines[i].strip().startswith(">") or
                             (quote and lines[i].strip() and not _block_starts(lines[i]))):
                if lines[i].strip().startswith(">"):
                    quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                else:
                    quote.append(lines[i])  # lazy continuation line
                i += 1
            inner, _ = _md_to_html("\n".join(quote))
            out.append(f"<blockquote>{inner}</blockquote>")
            continue

        # Pipe table — header row + separator (|---|---|) + body rows
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$", lines[i + 1]):
            flush_para()
            out.append(_render_table(lines, i, n))
            i += 2  # header + separator
            while i < n and "|" in lines[i] and lines[i].strip():
                i += 1
            continue

        # Lists (bullet or numbered) — indentation-aware + recursive, so nested
        # sub-lists render as real nested <ul>/<ol> instead of being flattened.
        if _LIST_ITEM.match(line):
            flush_para()
            html_list, i = _render_list(lines, i, n)
            out.append(html_list)
            continue

        # Blank line ends a paragraph
        if not stripped:
            flush_para()
            i += 1
            continue

        para.append(stripped)
        i += 1

    flush_para()
    return "\n".join(out), toc


def _render_table(lines: list[str], start: int, n: int) -> str:
    """Render a GitHub-style pipe table starting at `start` (header row)."""
    def cells(row: str) -> list[str]:
        row = row.strip()
        row = row[1:] if row.startswith("|") else row
        row = row[:-1] if row.endswith("|") else row
        return [c.strip() for c in row.split("|")]

    header = cells(lines[start])
    body_rows = []
    j = start + 2
    while j < n and "|" in lines[j] and lines[j].strip():
        body_rows.append(cells(lines[j]))
        j += 1

    thead = "".join(f"<th>{_inline(c)}</th>" for c in header)
    tbody = ""
    for r in body_rows:
        r = (r + [""] * len(header))[: len(header)]
        tbody += "<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>"
    return f"<table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"


# ---------------------------------------------------------------------------
# Embedded graphics — mermaid diagrams + data charts (both → inline SVG)
# ---------------------------------------------------------------------------

def _strip_svg_for_inline(svg: str) -> str:
    """Make a standalone SVG safe + responsive to inline inside the report:
    drop any XML/doctype prolog, force width:100% (mmdc emits a fixed width),
    and remove the absolute width/height attrs so it scales to the column."""
    # Drop <?xml ...?> and <!DOCTYPE ...> prologs if present.
    svg = re.sub(r"^\s*<\?xml[^>]*\?>", "", svg, flags=re.I).strip()
    svg = re.sub(r"^\s*<!DOCTYPE[^>]*>", "", svg, flags=re.I).strip()
    # Force the root <svg> to scale to the figure width.
    def _fix_root(m):
        tag = m.group(0)
        tag = re.sub(r'\swidth="[^"]*"', "", tag)
        tag = re.sub(r'\sheight="[^"]*"', "", tag)
        return tag[:-1] + ' style="width:100%;height:auto;max-width:100%">'
    return re.sub(r"<svg\b[^>]*>", _fix_root, svg, count=1)


def _render_mermaid_inline(code: str) -> str | None:
    """Render a ```mermaid block to an inline <figure><svg>…</figure>. Uses the
    shared render_mermaid_file (mmdc) → SVG, then inlines the file content so the
    report stays self-contained. None on any failure → caller shows raw code."""
    code = (code or "").strip()
    if not code:
        return None
    try:
        import os
        import tempfile
        from engine.tools.image_gen import render_mermaid_file
        fd, out_path = tempfile.mkstemp(suffix=".svg", prefix="report-mermaid-")
        os.close(fd)
        try:
            res = render_mermaid_file(code, out_path=out_path, fmt="svg",
                                      background="transparent")
            if not res or not os.path.exists(out_path):
                return None
            with open(out_path, "r", encoding="utf-8") as f:
                svg = f.read()
        finally:
            try:
                os.remove(out_path)
            except OSError:
                pass
        if "<svg" not in svg:
            return None
        return ('<figure class="report-figure report-diagram">'
                + _strip_svg_for_inline(svg) + "</figure>")
    except Exception:
        return None


def _render_chart_inline(spec_json: str) -> str | None:
    """Render a ```chart JSON spec to an inline <figure><svg>…</figure>."""
    try:
        from engine import report_charts
        svg = report_charts.render_chart_svg(spec_json)
        if not svg:
            return None
        return '<figure class="report-figure report-chart">' + svg + "</figure>"
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def _toc_html(toc: list[tuple[int, str, str]]) -> str:
    """Sticky-sidebar TOC. Returns "" when there's too little to warrant one."""
    if len(toc) < 3:
        return ""
    links = []
    for level, anchor, text in toc:
        cls = "depth-3" if level == 3 else "depth-2"
        links.append(f'<a href="#{anchor}" class="{cls}">{html.escape(_strip_inline_md(text))}</a>')
    return ('<aside class="toc-sidebar"><nav aria-label="Inhalt">'
            + "".join(links) + "</nav></aside>")


def _stats_html(stats: Optional[dict]) -> str:
    """The thin stats strip under the hero (Deep Research passes counts)."""
    if not stats:
        return ""
    order = [("rounds", "Runden"), ("queries", "Suchanfragen"),
             ("sources", "Quellen"), ("urls", "URLs"), ("duration", "Dauer")]
    chips = []
    for key, label in order:
        if key not in stats:
            continue
        chips.append(f'<div class="stat"><span class="stat-value">'
                     f'{html.escape(str(stats[key]))}</span> {label}</div>')
    if not chips:
        return ""
    return f'<div class="stats">{"".join(chips)}</div>'


def _meta_footer_html(meta: Optional[dict]) -> str:
    if not meta:
        return ""
    when = datetime.now().strftime("%d.%m.%Y %H:%M")
    dur = meta.get("duration_s") or 0
    dur_str = f"{int(dur // 60)} min {int(dur % 60)} s" if dur >= 60 else f"{dur:.1f} s"
    ti, to = meta.get("tokens_in", 0), meta.get("tokens_out", 0)
    cr = meta.get("cache_read_tokens", 0) or 0
    cost = meta.get("cost", 0)
    tokens_part = f"{ti:,} / {to:,} Tokens"
    if cr:
        tokens_part += f" · {cr:,} ⚡ gecacht"
    parts = [
        html.escape(str(meta.get("model", "—"))),
        when,
        dur_str,
        tokens_part,
        f"${cost:.4f}",
    ]
    return ('<footer class="report-footer">'
            + "  ·  ".join(parts)
            + "</footer>")


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return ""


def _sources_html(sources: Optional[list]) -> str:
    """Collapsible sources panel. Deep Research passes [{title,url,trust_hint}]."""
    if not sources:
        return ""
    items = []
    num = 0
    for s in sources:
        url = (s.get("url") or s.get("link") or "").strip()
        if not url:
            continue
        num += 1
        title = html.escape(s.get("title") or url)
        safe_url = html.escape(url, quote=True)
        dom = html.escape(_domain(url))
        items.append(
            f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
            f'<span class="snum">{num}.</span><span>{title}</span>'
            f'<span class="sdomain">{dom}</span></a>')
    if not items:
        return ""
    return ('<section class="sources-panel"><details open>'
            f'<summary>Quellen ({len(items)})</summary>'
            f'<div class="sources-list">{"".join(items)}</div>'
            "</details></section>")


# Toolbar + TOC scrollspy. Pure DOM, no external libs. Kept tiny.
_JS = """
(function(){
  var btn=document.getElementById('copy-btn');
  if(btn){btn.addEventListener('click',function(){
    navigator.clipboard&&navigator.clipboard.writeText(location.href);
    var t=btn.querySelector('.toast'); if(t){t.classList.add('show');
      setTimeout(function(){t.classList.remove('show');},1400);}
  });}
  var links=[].slice.call(document.querySelectorAll('.toc-sidebar nav a'));
  if(!links.length||!('IntersectionObserver' in window))return;
  var map={}; links.forEach(function(a){map[a.getAttribute('href').slice(1)]=a;});
  var io=new IntersectionObserver(function(es){
    es.forEach(function(e){
      if(e.isIntersecting){
        links.forEach(function(a){a.classList.remove('active');});
        var a=map[e.target.id]; if(a)a.classList.add('active');
      }
    });
  },{rootMargin:'-10% 0px -80% 0px'});
  document.querySelectorAll('.content h2[id],.content h3[id]').forEach(function(h){io.observe(h);});
})();
"""


_CSS = """
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --font-display: 'Charter', 'Iowan Old Style', 'Palatino Linotype', Georgia, serif;
  --font-body: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  --bg: #fbf9f4; --bg-surface: #ffffff; --bg-surface-alt: #f1ede4;
  --border: rgba(0,0,0,0.08); --border-strong: rgba(0,0,0,0.16);
  --text: #1a1817; --text-dim: #5a5651; --text-muted: #8a8580;
  --accent: #b8543a; --accent-light: #d97a5e; --accent-bg: rgba(184,84,58,0.06);
  --gold: #c9952e; --gold-bg: rgba(201,149,46,0.09);
  --aurora-a: rgba(184,84,58,0.10); --aurora-b: rgba(201,149,46,0.08); --aurora-c: rgba(64,98,128,0.07);
  --radius: 12px; --shadow-sm: 0 1px 3px rgba(0,0,0,0.05); --shadow-md: 0 4px 24px rgba(0,0,0,0.07);
  --max-w: 760px;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #131214; --bg-surface: #1c1a1e; --bg-surface-alt: #25232a;
    --border: rgba(255,255,255,0.07); --border-strong: rgba(255,255,255,0.16);
    --text: #ece8e2; --text-dim: #a8a39c; --text-muted: #6f6b66;
    --accent: #e88f73; --accent-light: #f4ad95; --accent-bg: rgba(232,143,115,0.09);
    --gold: #e8c05a; --gold-bg: rgba(232,192,90,0.09);
    --aurora-a: rgba(232,143,115,0.13); --aurora-b: rgba(232,192,90,0.09); --aurora-c: rgba(125,180,224,0.10);
    --shadow-sm: 0 1px 3px rgba(0,0,0,0.4); --shadow-md: 0 4px 28px rgba(0,0,0,0.55);
  }
}
html { scroll-behavior: smooth; scroll-padding-top: 4rem; }
body {
  font-family: var(--font-body); background: var(--bg); color: var(--text);
  line-height: 1.75; font-size: 17px; -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility; position: relative; min-height: 100vh;
}
/* Aurora background — slowly drifting accent blobs behind the content. */
body::before {
  content: ''; position: fixed; inset: -20vh -20vw; z-index: -2;
  background:
    radial-gradient(40vw 50vh at 18% 22%, var(--aurora-a) 0%, transparent 60%),
    radial-gradient(45vw 55vh at 82% 12%, var(--aurora-b) 0%, transparent 65%),
    radial-gradient(55vw 60vh at 50% 88%, var(--aurora-c) 0%, transparent 70%);
  filter: blur(20px); animation: aurora-drift 28s ease-in-out infinite alternate;
  pointer-events: none;
}
/* SVG film-grain so it doesn't read as flat CSS. */
body::after {
  content: ''; position: fixed; inset: 0; z-index: -1; pointer-events: none;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.32 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
  opacity: 0.045; mix-blend-mode: overlay;
}
@keyframes aurora-drift {
  0% { transform: translate3d(0,0,0) scale(1); }
  50% { transform: translate3d(2vw,-1vh,0) scale(1.04); }
  100% { transform: translate3d(-1vw,1.5vh,0) scale(1.02); }
}
@media (prefers-reduced-motion: reduce) { body::before { animation: none; } }

/* Toolbar */
.toolbar { position: fixed; top: 1rem; right: 1rem; z-index: 100; display: flex; gap: 0.4rem; opacity: 0.7; transition: opacity 0.2s; }
.toolbar:hover { opacity: 1; }
.toolbar button {
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 14px;
  border: 1px solid var(--border-strong); border-radius: 8px; background: var(--bg-surface);
  color: var(--text); font-family: inherit; font-size: 0.78rem; font-weight: 500;
  cursor: pointer; box-shadow: var(--shadow-sm); transition: background 0.15s; position: relative;
}
.toolbar button:hover { background: var(--bg-surface-alt); }
.toolbar .toast {
  position: absolute; top: calc(100% + 6px); right: 0; background: var(--text); color: var(--bg);
  padding: 4px 10px; border-radius: 6px; font-size: 0.72rem; white-space: nowrap;
  opacity: 0; transition: opacity 0.15s; pointer-events: none;
}
.toolbar .toast.show { opacity: 1; }

/* Hero */
.hero { position: relative; padding: 5.5rem 2rem 2.5rem; text-align: center; overflow: hidden; }
.hero::before {
  content: ''; position: absolute; inset: 0; pointer-events: none;
  background: radial-gradient(ellipse 70% 60% at 50% 40%, color-mix(in srgb, var(--accent) 10%, transparent) 0%, transparent 70%);
}
.hero::after {
  content: ''; position: absolute; left: 50%; bottom: 0; width: min(60%, 320px); height: 1px;
  transform: translateX(-50%); background: linear-gradient(90deg, transparent, var(--border-strong), transparent);
}
.hero-label {
  position: relative; text-transform: uppercase; letter-spacing: 0.28em; font-size: 0.68rem;
  font-weight: 600; color: var(--accent); opacity: 0.85; margin-bottom: 1.4rem;
}
.hero h1 {
  position: relative; font-family: var(--font-display); font-size: clamp(2rem, 4.5vw, 3rem);
  font-weight: 600; line-height: 1.15; max-width: 720px; margin: 0 auto;
  letter-spacing: -0.02em; color: var(--text);
}

/* Stats strip */
.stats {
  display: flex; justify-content: center; gap: 1.5rem; flex-wrap: wrap;
  padding: 0.9rem 2rem; background: var(--bg-surface); border-top: 1px solid var(--border);
  border-bottom: 1px solid var(--border); font-size: 0.82rem; color: var(--text-dim);
}
.stat { display: flex; align-items: center; gap: 0.35rem; }
.stat-value { font-weight: 600; color: var(--text); }

/* Layout: TOC sidebar + content */
.layout { display: grid; grid-template-columns: 200px 1fr; max-width: calc(var(--max-w) + 260px); margin: 0 auto; }
/* Narrow (incl. the in-app artifact panel): collapse the sidebar into a sticky,
   horizontally-scrolling strip at the top of the content instead of hiding it —
   the TOC stays reachable even when there's no room for a gutter. */
@media (max-width: 860px) {
  .layout { display: block; }
  .toc-sidebar {
    position: sticky; top: 0; z-index: 20; height: auto; max-height: none;
    display: block; overflow-x: auto; overflow-y: hidden; white-space: nowrap;
    padding: 0.55rem 0.8rem; border-right: none; border-bottom: 1px solid var(--border);
    background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(8px);
    -webkit-backdrop-filter: blur(8px);
  }
  .toc-sidebar nav { display: inline-flex; gap: 0.2rem; }
  .toc-sidebar nav a { display: inline-block; margin: 0; padding: 0.3rem 0.6rem; }
  .toc-sidebar nav a::before { display: none; }
  .toc-sidebar nav a.depth-3 { padding-left: 0.6rem; }
}

.toc-sidebar { position: sticky; top: 0; height: 100vh; overflow-y: auto; padding: 3.2rem 0.8rem 2rem 1.4rem; border-right: 1px solid var(--border); font-size: 0.78rem; }
.toc-sidebar nav a {
  position: relative; display: block; color: var(--text-dim); text-decoration: none;
  padding: 0.42rem 0.7rem 0.42rem 0.85rem; margin: 1px 0; border-radius: 6px; line-height: 1.4;
  transition: color 0.18s ease, background 0.18s ease, padding-left 0.18s ease;
}
.toc-sidebar nav a::before {
  content: ''; position: absolute; left: 0; top: 50%; width: 2px; height: 0;
  background: var(--accent); transform: translateY(-50%); border-radius: 1px;
  transition: height 0.18s ease, opacity 0.18s ease; opacity: 0;
}
.toc-sidebar nav a:hover { color: var(--text); background: var(--accent-bg); padding-left: 1rem; }
.toc-sidebar nav a:hover::before { height: 60%; opacity: 1; }
.toc-sidebar nav a.active { color: var(--accent); font-weight: 600; background: var(--accent-bg); }
.toc-sidebar nav a.active::before { height: 80%; opacity: 1; }
.toc-sidebar nav a.depth-3 { padding-left: 1.3rem; font-size: 0.72rem; color: var(--text-muted); }
.toc-sidebar nav a.depth-3:hover { padding-left: 1.45rem; }

/* Content */
.content { max-width: var(--max-w); padding: 3rem 2.5rem 4rem; }
.content h2 {
  font-family: var(--font-display); font-size: clamp(1.55rem, 2.4vw, 1.85rem); font-weight: 600;
  margin: 3rem 0 1rem; padding-bottom: 0.55rem; border-bottom: 1px solid transparent;
  border-image: linear-gradient(90deg, var(--accent) 0%, transparent 65%) 1;
  letter-spacing: -0.022em; line-height: 1.2; color: var(--text);
}
.content h2:first-child { margin-top: 0; }
.content h3 { font-family: var(--font-display); font-size: 1.22rem; font-weight: 600; margin: 2.2rem 0 0.6rem; letter-spacing: -0.015em; color: var(--text); }
.content h4 { font-family: var(--font-body); font-size: 0.78rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.12em; color: var(--text-dim); margin: 1.6rem 0 0.5rem; }
.content p { margin-bottom: 1.1rem; }
/* Editorial drop cap on the opening paragraph. */
.content > p:first-of-type::first-letter,
.content > h2:first-child + p::first-letter {
  font-family: var(--font-display); font-weight: 700; font-size: 3.6em; line-height: 0.85;
  float: left; margin: 0.15em 0.12em 0 -0.04em; color: var(--accent);
}
.content a { color: var(--accent); text-decoration: underline; text-decoration-color: color-mix(in srgb, var(--accent) 35%, transparent); text-decoration-thickness: 1.5px; text-underline-offset: 3px; transition: text-decoration-color 0.15s, color 0.15s; }
.content a:hover { text-decoration-color: var(--accent); color: var(--accent-light); }
.content ul, .content ol { margin: 0 0 1.1rem 1.6rem; }
.content li > ul, .content li > ol { margin: 0.3rem 0 0.3rem 1.4rem; }
.content li { margin-bottom: 0.4rem; }
.content li::marker { color: var(--accent); }
.content blockquote {
  position: relative; border-left: 3px solid var(--gold); background: var(--gold-bg);
  padding: 1.1rem 1.4rem 1.1rem 2.6rem; margin: 1.5rem 0; border-radius: 0 var(--radius) var(--radius) 0;
  color: var(--text); font-family: var(--font-display); font-style: italic; font-size: 1.05rem; line-height: 1.55;
}
.content blockquote::before { content: '\\201C'; position: absolute; left: 0.5rem; top: 0.3rem; font-family: var(--font-display); font-size: 3rem; font-style: normal; color: var(--gold); opacity: 0.5; line-height: 1; }
.content hr { border: none; height: 1px; background: linear-gradient(90deg, transparent, var(--border-strong), transparent); margin: 2rem 0; }
.content del { color: var(--text-muted); text-decoration-color: var(--accent); }
.content code { font-family: var(--font-mono); font-size: 0.86em; background: var(--bg-surface-alt); padding: 0.15em 0.4em; border-radius: 4px; }
.content pre { background: var(--bg-surface-alt); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.25rem 1.5rem; overflow-x: auto; margin: 1.25rem 0; font-size: 0.86rem; line-height: 1.6; }
.content pre code { background: none; padding: 0; }
.content table { width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: 0.9rem; border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow-sm); }
.content th { text-align: left; padding: 0.7rem 1rem; background: var(--accent-bg); font-weight: 600; border-bottom: 2px solid var(--border-strong); }
.content td { padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.content tr:last-child td { border-bottom: none; }
.content tr:hover td { background: var(--accent-bg); }

/* Embedded graphics — mermaid diagrams, data charts, source images */
.report-figure { margin: 1.75rem 0; text-align: center; }
.report-figure svg { display: block; margin: 0 auto; max-width: 100%; height: auto; }
.report-diagram svg, .report-chart svg {
  background: var(--bg-surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 1rem; box-shadow: var(--shadow-sm);
}
.report-figure img {
  max-width: 100%; height: auto; border-radius: var(--radius);
  border: 1px solid var(--border); box-shadow: var(--shadow-sm);
}
.report-figure figcaption { margin-top: 0.5rem; font-size: 0.8rem; color: var(--text-muted); font-style: italic; }
/* ::kpi stat strip — coloured headline-metric boxes. */
.kpi-strip { display: flex; flex-wrap: wrap; gap: 0.9rem; margin: 1.75rem 0; }
.kpi-box {
  flex: 1 1 140px; min-width: 120px; text-align: center; padding: 1.1rem 1rem;
  border-radius: var(--radius); background: var(--kpi-bg); border: 1px solid var(--border);
  box-shadow: var(--shadow-sm); display: flex; flex-direction: column; gap: 0.3rem;
}
.kpi-box .kpi-value { font-family: var(--font-display); font-size: 1.7rem; font-weight: 700; line-height: 1.05; color: var(--kpi-fg); }
.kpi-box .kpi-label { font-size: 0.68rem; font-weight: 600; letter-spacing: 0.1em; text-transform: uppercase; color: var(--text-dim); }
@media (prefers-color-scheme: dark) { .kpi-box { filter: saturate(1.15) brightness(0.92); } }
/* Hero image — sits below the headline, full content width. */
.hero-image { max-width: var(--max-w); margin: 0 auto 1rem; padding: 0 2rem; }
.hero-image img {
  width: 100%; height: clamp(180px, 28vh, 320px); object-fit: cover;
  border-radius: var(--radius); border: 1px solid var(--border); box-shadow: var(--shadow-md);
}

/* Sources */
.sources-panel { margin-top: 3rem; border-top: 2px solid var(--border); padding-top: 1.5rem; }
.sources-panel summary { display: flex; align-items: center; gap: 0.5rem; cursor: pointer; font-size: 1rem; font-weight: 600; color: var(--text); padding: 0.5rem 0; list-style: none; user-select: none; }
.sources-panel summary::-webkit-details-marker { display: none; }
.sources-panel summary::before { content: '\\25B6'; font-size: 0.65em; color: var(--text-muted); transition: transform 0.2s; }
.sources-panel details[open] summary::before { transform: rotate(90deg); }
.sources-list { padding: 0.5rem 0 0 0.25rem; }
.sources-list a { display: flex; align-items: baseline; gap: 0.5rem; padding: 0.35rem 0; font-size: 0.85rem; color: var(--text); text-decoration: none; transition: color 0.15s; }
.sources-list a:hover { color: var(--accent); }
.sources-list .snum { color: var(--text-muted); font-size: 0.75rem; min-width: 1.5rem; text-align: right; flex-shrink: 0; }
.sources-list .sdomain { color: var(--text-muted); font-size: 0.75rem; margin-left: auto; flex-shrink: 0; }

/* Inline citation chips + the 'Belege' legend. The chip is a compact superscript
   [n] that jumps to the matching legend row; the row shows source + verbatim quote. */
.cite-chip { text-decoration: none; color: var(--accent); font-weight: 600; white-space: nowrap; }
.cite-chip sup { font-size: 0.7em; padding: 0 0.05em; }
.cite-chip:hover { color: var(--accent-light); }
.citations-panel { margin-top: 3rem; border-top: 2px solid var(--border); padding-top: 1.5rem; }
.citations-panel summary { display: flex; align-items: center; gap: 0.5rem; cursor: pointer; font-size: 1rem; font-weight: 600; color: var(--text); padding: 0.5rem 0; list-style: none; user-select: none; }
.citations-panel summary::-webkit-details-marker { display: none; }
.citations-panel summary::before { content: '\\25B6'; font-size: 0.65em; color: var(--text-muted); transition: transform 0.2s; }
.citations-panel details[open] summary::before { transform: rotate(90deg); }
.citations-list { list-style: none; padding: 0.5rem 0 0 0; margin: 0; counter-reset: none; }
.citations-list li { padding: 0.45rem 0; font-size: 0.85rem; line-height: 1.5; border-bottom: 1px solid var(--border); scroll-margin-top: 4rem; }
.citations-list li:last-child { border-bottom: none; }
.citations-list li:target { background: var(--accent-bg); border-radius: 6px; padding-left: 0.5rem; padding-right: 0.5rem; }
.citations-list .cite-n { color: var(--accent); font-weight: 600; margin-right: 0.4rem; }
.citations-list .cite-src { color: var(--text-dim); font-weight: 600; }
.citations-list .cite-quote { display: block; margin-top: 0.2rem; color: var(--text); font-family: var(--font-display); font-style: italic; }

.report-footer { text-align: center; padding: 2rem; font-size: 0.75rem; color: var(--text-muted); border-top: 1px solid var(--border); margin-top: 2rem; }

@media (prefers-reduced-motion: no-preference) {
  .content h2, .content h3, .content p, .content ul, .content ol, .content blockquote, .content table, .content pre {
    animation: fadeUp 0.4s ease both;
  }
  @keyframes fadeUp { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
}
@media print {
  .toc-sidebar, .toolbar { display: none !important; }
  .layout { grid-template-columns: 1fr; }
  body::before, body::after { display: none; }
  body { font-size: 11pt; }
  a { color: var(--text); }
}
"""


# Per-category hero-banner accent (base hue, in the warm editorial family). When
# no real hero image is supplied we synthesise a self-contained SVG banner in this
# hue so every report has a lead visual — no network, no model call, deterministic.
_HERO_HUE = {
    "product":    ("#b8543a", "#c9952e"),  # terracotta → gold
    "comparison": ("#40628a", "#5a8fb8"),  # slate blue
    "howto":      ("#5a7d4a", "#8bad6a"),  # sage green
    "factcheck":  ("#8a4a6a", "#b87a9a"),  # plum
    "report":     ("#b8543a", "#c9952e"),  # terracotta → gold (default)
    "studio":     ("#6a5a8a", "#9a8ab8"),  # muted violet
}


def _hero_banner_svg(title: str, category: Optional[str]) -> str:
    """A deterministic, self-contained SVG banner used as the lead visual when no
    real hero image is available. Warm gradient + soft geometric arcs keyed off the
    category hue; the report title seeds the arc geometry so different reports look
    distinct. Returned as a data-URI so the HTML stays fully offline-portable."""
    c1, c2 = _HERO_HUE.get(category or "report", _HERO_HUE["report"])
    # Seed a few positions from the title so banners differ report-to-report but
    # stay stable for the same title (no Date/random — matches the module rules).
    seed = sum(ord(ch) for ch in (title or "Bericht")) or 7
    a = 8 + (seed % 22)          # first arc x-offset
    b = 55 + (seed * 3 % 30)     # second arc x-offset
    r1 = 26 + (seed % 10)        # arc radii
    r2 = 34 + (seed * 2 % 12)
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 300' "
        f"preserveAspectRatio='xMidYMid slice'>"
        f"<defs>"
        f"<linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        f"<stop offset='0' stop-color='{c1}'/><stop offset='1' stop-color='{c2}'/>"
        f"</linearGradient>"
        f"<radialGradient id='v' cx='0.5' cy='0.35' r='0.75'>"
        f"<stop offset='0' stop-color='#ffffff' stop-opacity='0.16'/>"
        f"<stop offset='1' stop-color='#000000' stop-opacity='0.18'/>"
        f"</radialGradient></defs>"
        f"<rect width='1200' height='300' fill='url(#g)'/>"
        f"<g fill='none' stroke='#ffffff' stroke-opacity='0.14' stroke-width='2'>"
        f"<circle cx='{a*10}' cy='120' r='{r1*4}'/>"
        f"<circle cx='{b*12}' cy='210' r='{r2*4}'/>"
        f"<circle cx='980' cy='90' r='150'/>"
        f"</g>"
        f"<g fill='#ffffff' fill-opacity='0.10'>"
        f"<circle cx='{b*12}' cy='210' r='{r2*2}'/>"
        f"<circle cx='140' cy='60' r='40'/>"
        f"</g>"
        f"<rect width='1200' height='300' fill='url(#v)'/>"
        f"</svg>"
    )
    from urllib.parse import quote as _urlquote
    return "data:image/svg+xml;utf8," + _urlquote(svg, safe="")


def _hero_image_html(hero_image: Optional[str], title: str = "",
                     category: Optional[str] = None) -> str:
    """A full-width hero image below the headline. A real http(s) URL is embedded
    (data:/javascript: refused); otherwise a synthesised SVG banner is used so
    every report has a lead visual."""
    if hero_image and isinstance(hero_image, str) and re.match(r"^https?://", hero_image.strip(), re.I):
        safe = html.escape(hero_image.strip(), quote=True)
        return (f'<div class="hero-image"><img src="{safe}" alt="" loading="lazy" '
                f'referrerpolicy="no-referrer"></div>')
    banner = html.escape(_hero_banner_svg(title, category), quote=True)
    return f'<div class="hero-image hero-banner"><img src="{banner}" alt=""></div>'


def render_report_html(
    markdown: str,
    title: str,
    meta: Optional[dict] = None,
    sources: Optional[list] = None,
    category: Optional[str] = None,
    stats: Optional[dict] = None,
    hero_image: Optional[str] = None,
    doc_dir: Optional[str] = None,
) -> str:
    """Render a complete, self-contained HTML report document.

    Args:
        markdown: the report body in our markdown subset. May contain ```mermaid
            and ```chart fenced blocks, which render to inline SVG figures, and
            standard ![alt](url) image markdown — http(s) URLs are embedded and
            LOCAL paths (relative to `doc_dir`) are inlined as base64 data-URIs.
        title: report title (hero headline).
        meta: optional {model, tokens_in, tokens_out, cost, duration_s} footer.
        sources: optional [{title, url, trust_hint}] curated source list.
        category: one of _CATEGORY_LABEL keys → hero eyebrow label.
        stats: optional {rounds, queries, sources, urls, duration} stats strip.
        hero_image: optional https URL of a lead image (e.g. an OG image from the
            top source) shown full-width below the headline. When absent, a
            synthesised SVG banner is used instead.
        doc_dir: directory the report is written into — used to resolve relative
            image paths in the markdown so they can be inlined.
    """
    label = _CATEGORY_LABEL.get(category or "report", _CATEGORY_LABEL["report"])
    _tok = _render_doc_dir.set(doc_dir or "")
    # Pull inline [Quelle: …] citations out into numbered chips + a legend, so the
    # long source brackets don't clutter the prose (parity with the chat view).
    body_src, inline_citations = _extract_citations(markdown or "")
    _ctok = _render_citations.set(inline_citations)
    try:
        # A leading "# Title" in the body is redundant with the hero — drop it.
        body_md = re.sub(r"^\s*#\s+.+\n+", "", body_src, count=1)
        body_html, toc = _md_to_html(body_md)
    finally:
        _render_doc_dir.reset(_tok)
        _render_citations.reset(_ctok)
    citations_legend = _citations_legend_html(inline_citations)
    # The title is placed verbatim into <title> and the <h1> (no tags allowed
    # there), so strip inline markdown — a model writing '# **Titel**' otherwise
    # leaves the ** visible in the headline.
    safe_title = html.escape(_strip_inline_md(title) or "Bericht")
    toc_html = _toc_html(toc)
    # Without a TOC the content column shouldn't reserve the 200px gutter.
    layout_open = '<div class="layout">' if toc_html else '<div class="layout" style="grid-template-columns:1fr">'

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{safe_title}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="toolbar">
  <button id="copy-btn" title="Link kopieren">Link kopieren<span class="toast">Kopiert</span></button>
  <button onclick="window.print()" title="Drucken oder als PDF speichern">Drucken / PDF</button>
</div>
<header class="hero">
  <div class="hero-label">{html.escape(label)}</div>
  <h1>{safe_title}</h1>
</header>
{_hero_image_html(hero_image, title, category)}
{_stats_html(stats)}
{layout_open}
  {toc_html}
  <main>
    <article class="content">
      {body_html}
    </article>
    {citations_legend}
    {_sources_html(sources)}
    {_meta_footer_html(meta)}
  </main>
</div>
<script>{_JS}</script>
</body>
</html>
"""
