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

import html
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

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
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


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

    def _link(m):
        label, url = m.group(1), m.group(2)
        # Only http(s)/mailto become anchors; anything else stays plain text
        # (defends against javascript:/data: even though input is our own).
        if not re.match(r"^(https?:|mailto:)", url, re.I):
            return html.escape(m.group(0), quote=False)
        safe_url = html.escape(url, quote=True)
        return (f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer">'
                f"{label}</a>")

    return _LINK.sub(_link, out)


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

        # Fenced code block
        if stripped.startswith("```"):
            flush_para()
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence
            body = html.escape("\n".join(code), quote=False)
            out.append(f"<pre><code>{body}</code></pre>")
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

        # Blockquote (possibly multi-line)
        if stripped.startswith(">"):
            flush_para()
            quote: list[str] = []
            while i < n and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{_inline(' '.join(quote).strip())}</blockquote>")
            continue

        # Pipe table — header row + separator (|---|---|) + body rows
        if "|" in line and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]+\|?\s*$", lines[i + 1]):
            flush_para()
            out.append(_render_table(lines, i, n))
            i += 2  # header + separator
            while i < n and "|" in lines[i] and lines[i].strip():
                i += 1
            continue

        # Lists (bullet or numbered)
        if re.match(r"^\s*([-*+]|\d+\.)\s+", line):
            flush_para()
            ordered = bool(re.match(r"^\s*\d+\.\s+", line))
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < n and re.match(r"^\s*([-*+]|\d+\.)\s+", lines[i]):
                item = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", lines[i])
                items.append(f"<li>{_inline(item.strip())}</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
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
# Page assembly
# ---------------------------------------------------------------------------

def _toc_html(toc: list[tuple[int, str, str]]) -> str:
    """Sticky-sidebar TOC. Returns "" when there's too little to warrant one."""
    if len(toc) < 3:
        return ""
    links = []
    for level, anchor, text in toc:
        cls = "depth-3" if level == 3 else "depth-2"
        links.append(f'<a href="#{anchor}" class="{cls}">{html.escape(text)}</a>')
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
    cost = meta.get("cost", 0)
    parts = [
        html.escape(str(meta.get("model", "—"))),
        when,
        dur_str,
        f"{ti:,} / {to:,} Tokens",
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
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .toc-sidebar { display: none; } }

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
.content li { margin-bottom: 0.4rem; }
.content li::marker { color: var(--accent); }
.content blockquote {
  position: relative; border-left: 3px solid var(--gold); background: var(--gold-bg);
  padding: 1.1rem 1.4rem 1.1rem 2.6rem; margin: 1.5rem 0; border-radius: 0 var(--radius) var(--radius) 0;
  color: var(--text); font-family: var(--font-display); font-style: italic; font-size: 1.05rem; line-height: 1.55;
}
.content blockquote::before { content: '\\201C'; position: absolute; left: 0.5rem; top: 0.3rem; font-family: var(--font-display); font-size: 3rem; font-style: normal; color: var(--gold); opacity: 0.5; line-height: 1; }
.content hr { border: none; height: 1px; background: linear-gradient(90deg, transparent, var(--border-strong), transparent); margin: 2rem 0; }
.content code { font-family: var(--font-mono); font-size: 0.86em; background: var(--bg-surface-alt); padding: 0.15em 0.4em; border-radius: 4px; }
.content pre { background: var(--bg-surface-alt); border: 1px solid var(--border); border-radius: var(--radius); padding: 1.25rem 1.5rem; overflow-x: auto; margin: 1.25rem 0; font-size: 0.86rem; line-height: 1.6; }
.content pre code { background: none; padding: 0; }
.content table { width: 100%; border-collapse: collapse; margin: 1.25rem 0; font-size: 0.9rem; border-radius: var(--radius); overflow: hidden; box-shadow: var(--shadow-sm); }
.content th { text-align: left; padding: 0.7rem 1rem; background: var(--accent-bg); font-weight: 600; border-bottom: 2px solid var(--border-strong); }
.content td { padding: 0.6rem 1rem; border-bottom: 1px solid var(--border); vertical-align: top; }
.content tr:last-child td { border-bottom: none; }
.content tr:hover td { background: var(--accent-bg); }

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


def render_report_html(
    markdown: str,
    title: str,
    meta: Optional[dict] = None,
    sources: Optional[list] = None,
    category: Optional[str] = None,
    stats: Optional[dict] = None,
) -> str:
    """Render a complete, self-contained HTML report document.

    Args:
        markdown: the report body in our markdown subset.
        title: report title (hero headline).
        meta: optional {model, tokens_in, tokens_out, cost, duration_s} footer.
        sources: optional [{title, url, trust_hint}] curated source list.
        category: one of _CATEGORY_LABEL keys → hero eyebrow label.
        stats: optional {rounds, queries, sources, urls, duration} stats strip.
    """
    label = _CATEGORY_LABEL.get(category or "report", _CATEGORY_LABEL["report"])
    # A leading "# Title" in the body is redundant with the hero — drop it.
    body_md = re.sub(r"^\s*#\s+.+\n+", "", markdown or "", count=1)
    body_html, toc = _md_to_html(body_md)
    safe_title = html.escape(title or "Bericht")
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
{_stats_html(stats)}
{layout_open}
  {toc_html}
  <main>
    <article class="content">
      {body_html}
    </article>
    {_sources_html(sources)}
    {_meta_footer_html(meta)}
  </main>
</div>
<script>{_JS}</script>
</body>
</html>
"""
