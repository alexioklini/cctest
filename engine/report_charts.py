# Dependency-free SVG chart generator for report HTML.
#
# The deep-research synthesis model emits a fenced ```chart block holding a small
# JSON spec; report_html turns it into an inline <svg> via this module. NO new
# dependencies (the main server has no matplotlib/plotly) — we hand-emit SVG,
# the same self-contained-by-construction approach report_html uses for the rest
# of the document.
#
# Spec shape (lenient — bad/missing fields degrade to a skipped chart, never a crash):
#   {"type": "bar"|"line"|"pie", "title": "...",
#    "data": [{"label": "Q1", "value": 170}, {"label": "Air75", "value": 120}, ...]}
#
# Colours come from the report's warm editorial palette (terracotta/gold/teal/…)
# so charts sit in the same visual language. All text is HTML-escaped.

import html
import json
import math

# Palette — the report accent family, cycled across series/slices.
_PALETTE = ["#b8543a", "#c9952e", "#4d7ea8", "#5a8a5a", "#8a5a8a", "#d97a5e",
            "#a8763a", "#3a8a8a"]
_TEXT = "#1a1817"
_MUTED = "#8a8580"
_GRID = "rgba(0,0,0,0.10)"

_W = 640          # logical viewBox width
_PAD_L = 56       # left padding (y-axis labels)
_PAD_R = 20
_PAD_T = 44       # top (title)
_PAD_B = 56       # bottom (x-axis labels)


def _coerce_data(spec):
    """Return [(label:str, value:float)] from a spec, dropping unusable rows.
    None if there's nothing chartable."""
    rows = []
    for d in (spec.get("data") or []):
        if not isinstance(d, dict):
            continue
        label = str(d.get("label", "")).strip()
        try:
            value = float(d.get("value"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):
            continue
        rows.append((label, value))
    return rows or None


def _nice_ceil(v):
    """Round an axis max up to a 'nice' number (1/2/5 × 10^n)."""
    if v <= 0:
        return 1.0
    exp = math.floor(math.log10(v))
    base = 10 ** exp
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * base:
            return m * base
    return 10 * base


def _title_svg(title, w):
    if not title:
        return ""
    return (f'<text x="{w/2:.0f}" y="26" text-anchor="middle" '
            f'font-size="16" font-weight="700" fill="{_TEXT}" '
            f'font-family="Charter, Georgia, serif">{html.escape(title)}</text>')


def _bar_chart(rows, title):
    n = len(rows)
    h = 360
    plot_w = _W - _PAD_L - _PAD_R
    plot_h = h - _PAD_T - _PAD_B
    vmax = _nice_ceil(max(v for _, v in rows))
    gap = plot_w / n
    bar_w = min(gap * 0.62, 80)
    parts = [_title_svg(title, _W)]
    # Y grid + labels (4 ticks)
    for i in range(5):
        gv = vmax * i / 4
        y = _PAD_T + plot_h - (gv / vmax) * plot_h
        parts.append(f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_W-_PAD_R}" y2="{y:.1f}" '
                     f'stroke="{_GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{_PAD_L-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="{_MUTED}">{_fmt_num(gv)}</text>')
    # Bars
    for i, (label, value) in enumerate(rows):
        bh = (value / vmax) * plot_h
        x = _PAD_L + gap * i + (gap - bar_w) / 2
        y = _PAD_T + plot_h - bh
        color = _PALETTE[i % len(_PALETTE)]
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{bh:.1f}" '
                     f'rx="3" fill="{color}"/>')
        parts.append(f'<text x="{x+bar_w/2:.1f}" y="{y-6:.1f}" text-anchor="middle" '
                     f'font-size="11" font-weight="600" fill="{_TEXT}">{_fmt_num(value)}</text>')
        parts.append(f'<text x="{_PAD_L+gap*i+gap/2:.1f}" y="{h-_PAD_B+20:.0f}" '
                     f'text-anchor="middle" font-size="11" fill="{_MUTED}">'
                     f'{html.escape(label[:16])}</text>')
    return _wrap_svg(parts, h)


def _line_chart(rows, title):
    h = 360
    plot_w = _W - _PAD_L - _PAD_R
    plot_h = h - _PAD_T - _PAD_B
    vmax = _nice_ceil(max(v for _, v in rows))
    n = len(rows)
    step = plot_w / max(1, n - 1)
    parts = [_title_svg(title, _W)]
    for i in range(5):
        gv = vmax * i / 4
        y = _PAD_T + plot_h - (gv / vmax) * plot_h
        parts.append(f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_W-_PAD_R}" y2="{y:.1f}" '
                     f'stroke="{_GRID}" stroke-width="1"/>')
        parts.append(f'<text x="{_PAD_L-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="{_MUTED}">{_fmt_num(gv)}</text>')
    pts = []
    for i, (label, value) in enumerate(rows):
        x = _PAD_L + step * i
        y = _PAD_T + plot_h - (value / vmax) * plot_h
        pts.append((x, y))
        parts.append(f'<text x="{x:.1f}" y="{h-_PAD_B+20:.0f}" text-anchor="middle" '
                     f'font-size="11" fill="{_MUTED}">{html.escape(label[:16])}</text>')
    path = "M " + " L ".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    parts.append(f'<path d="{path}" fill="none" stroke="{_PALETTE[0]}" stroke-width="2.5" '
                 f'stroke-linejoin="round" stroke-linecap="round"/>')
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="{_PALETTE[0]}"/>')
    return _wrap_svg(parts, h)


def _pie_chart(rows, title):
    h = 360
    total = sum(v for _, v in rows if v > 0)
    if total <= 0:
        return None
    cx, cy, r = _W * 0.36, _PAD_T + (h - _PAD_T - 20) / 2, 120
    parts = [_title_svg(title, _W)]
    ang = -math.pi / 2  # start at top
    legend_y = _PAD_T + 10
    for i, (label, value) in enumerate(rows):
        if value <= 0:
            continue
        frac = value / total
        a2 = ang + frac * 2 * math.pi
        x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        large = 1 if frac > 0.5 else 0
        color = _PALETTE[i % len(_PALETTE)]
        parts.append(f'<path d="M {cx:.1f} {cy:.1f} L {x1:.1f} {y1:.1f} '
                     f'A {r} {r} 0 {large} 1 {x2:.1f} {y2:.1f} Z" fill="{color}"/>')
        # Legend
        ly = legend_y + i * 24
        parts.append(f'<rect x="{_W*0.62:.0f}" y="{ly:.0f}" width="14" height="14" rx="3" fill="{color}"/>')
        parts.append(f'<text x="{_W*0.62+22:.0f}" y="{ly+12:.0f}" font-size="12" fill="{_TEXT}">'
                     f'{html.escape(label[:22])} ({frac*100:.0f}%)</text>')
        ang = a2
    return _wrap_svg(parts, h)


def _fmt_num(v):
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.1f}"


def _wrap_svg(parts, h):
    return (f'<svg viewBox="0 0 {_W} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" style="width:100%;height:auto">'
            + "".join(parts) + "</svg>")


def render_chart_svg(spec_json: str) -> str | None:
    """Turn a ```chart JSON spec string into an inline SVG string. Returns None on
    any problem (bad JSON / no data / unknown type) so the caller can fall back to
    showing the raw block."""
    try:
        spec = json.loads(spec_json)
    except (ValueError, TypeError):
        return None
    if not isinstance(spec, dict):
        return None
    rows = _coerce_data(spec)
    if not rows:
        return None
    ctype = str(spec.get("type", "bar")).lower()
    title = str(spec.get("title", "")).strip()
    try:
        if ctype == "line":
            return _line_chart(rows, title)
        if ctype == "pie":
            return _pie_chart(rows, title)
        return _bar_chart(rows, title)  # default
    except Exception:
        return None
