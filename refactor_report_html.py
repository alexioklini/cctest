#!/usr/bin/env python3
"""Render REFACTOR_REPORT.md -> REFACTOR_REPORT.html (zero-dependency).

HTML is a GENERATED VIEW for easy remote reading — never hand-edited and never a
second source of truth. The markdown file is authoritative; if they ever disagree,
regenerate. Handles only the markdown subset the report uses: ATX headings, GFM
pipe tables, -/* bullet lists, **bold**, `code`, > blockquotes, --- rules, and the
status emojis (✅⬜🔄⛔🚫) which become colored badges.

Run:  /opt/homebrew/bin/python3 refactor_report_html.py
"""
from __future__ import annotations
import html, re, sys, datetime
from pathlib import Path

SRC = Path(__file__).with_name("REFACTOR_REPORT.md")
OUT = Path(__file__).with_name("REFACTOR_REPORT.html")

BADGE = {  # emoji -> (label, css class)
    "✅": ("done", "done"), "⬜": ("planned", "planned"),
    "🔄": ("in progress", "wip"), "⛔": ("gated", "gated"),
    "🚫": ("excluded", "excluded"), "⚠️": ("attention", "warn"),
}

def _inline(text: str) -> str:
    """Escape, then apply inline markdown. Operates on already-escaped text."""
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    for emo, (label, cls) in BADGE.items():
        text = text.replace(emo, f'<span class="badge {cls}" title="{label}">{emo}</span>')
    return text

def convert(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    in_list = False
    def close_list():
        nonlocal in_list
        if in_list:
            out.append("</ul>"); in_list = False
    while i < n:
        ln = lines[i]
        # table block: a header row followed by a |---| separator
        if "|" in ln and i + 1 < n and re.match(r"^\s*\|?[\s:|-]+\|[\s:|-]*$", lines[i+1]):
            close_list()
            header = [c.strip() for c in ln.strip().strip("|").split("|")]
            out.append('<table><thead><tr>' +
                       "".join(f"<th>{_inline(c)}</th>" for c in header) +
                       "</tr></thead><tbody>")
            i += 2
            while i < n and "|" in lines[i] and lines[i].strip():
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                out.append("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells) + "</tr>")
                i += 1
            out.append("</tbody></table>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", ln)
        if m:
            close_list(); lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>"); i += 1; continue
        if re.match(r"^\s*[-*]\s+", ln):
            if not in_list: out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(re.sub(r'^\s*[-*]\s+', '', ln))}</li>"); i += 1; continue
        if ln.startswith(">"):
            close_list()
            out.append(f"<blockquote>{_inline(ln.lstrip('> ').rstrip())}</blockquote>"); i += 1; continue
        if re.match(r"^---+\s*$", ln):
            close_list(); out.append("<hr>"); i += 1; continue
        if not ln.strip():
            close_list(); i += 1; continue
        close_list(); out.append(f"<p>{_inline(ln)}</p>"); i += 1
    close_list()
    return "\n".join(out)

CSS = """
:root{color-scheme:light dark}
body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:1100px;
 margin:2rem auto;padding:0 1.2rem;color:#1a1a1a;background:#fff}
@media(prefers-color-scheme:dark){body{color:#e6e6e6;background:#16181c}
 table{border-color:#333}th{background:#23262b}td,th{border-color:#333}
 code{background:#23262b}blockquote{background:#1d2025;border-color:#444}}
h1{font-size:1.7rem;border-bottom:2px solid #ddd;padding-bottom:.3rem}
h2{font-size:1.3rem;margin-top:2rem;border-bottom:1px solid #eee;padding-bottom:.2rem}
h3{font-size:1.08rem;margin-top:1.4rem}
table{border-collapse:collapse;width:100%;margin:.8rem 0;font-size:.9rem}
th,td{border:1px solid #ddd;padding:.4rem .6rem;text-align:left;vertical-align:top}
th{background:#f4f5f7}
code{background:#f0f1f3;padding:.1rem .3rem;border-radius:3px;font-size:.88em}
blockquote{background:#f7f8fa;border-left:4px solid #c5c9d0;margin:.8rem 0;
 padding:.5rem .9rem;border-radius:0 4px 4px 0}
hr{border:none;border-top:1px solid #e3e3e3;margin:1.6rem 0}
.badge{font-style:normal}
.meta{color:#888;font-size:.82rem}
"""

def main() -> int:
    if not SRC.exists():
        print(f"missing {SRC}", file=sys.stderr); return 1
    body = convert(SRC.read_text(encoding="utf-8"))
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    doc = (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
           f"<meta name=viewport content='width=device-width,initial-scale=1'>"
           f"<title>Refactor Progress Report</title><style>{CSS}</style></head><body>"
           f"<p class=meta>Generated from REFACTOR_REPORT.md · {ts} · "
           f"this HTML is a view; the .md is the source of truth.</p>"
           f"{body}</body></html>")
    OUT.write_text(doc, encoding="utf-8")
    print(f"wrote {OUT} ({len(doc):,} bytes)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
