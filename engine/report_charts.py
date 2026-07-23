# Dependency-free SVG chart generator for report HTML — infographic edition.
#
# The deep-research / report synthesis model emits a fenced ```chart block
# holding a small JSON spec; report_html turns it into an inline <svg> via this
# module. NO new dependencies (the main server has no matplotlib/plotly) — we
# hand-emit SVG, the same self-contained-by-construction approach report_html
# uses for the rest of the document.
#
# Spec shape (lenient — bad/missing fields degrade to a skipped chart, never a crash):
#   {"type": "bar"|"line"|"pie"|"donut"|"funnel", "title": "...",
#    "data": [{"label": "Q1", "value": 170, "icon": "rocket"}, ...]}
# `icon` (optional) is a Font-Awesome-6 SOLID icon name (e.g. "rocket",
# "lightbulb", "handshake"); the vector path is embedded from the vendored
# web/vendor/fontawesome/solid-paths.json — output stays self-contained.
#
# Look: infographic style — per-series gradients, soft drop shadows, donut with
# a white center medallion and icon callouts, funnel with icon badges, smooth
# area lines. Colours come from the report's warm editorial palette
# (terracotta/gold/teal/…) so charts sit in the same visual language. All text
# is HTML-escaped.

import html
import json
import math
import os

# Palette — the report accent family, cycled across series/slices.
_PALETTE = ["#b8543a", "#c9952e", "#4d7ea8", "#5a8a5a", "#8a5a8a", "#d97a5e",
            "#a8763a", "#3a8a8a"]
_TEXT = "#1a1817"
_MUTED = "#8a8580"
_GRID = "rgba(0,0,0,0.10)"
_SERIF = "Charter, Georgia, serif"
_SANS = "system-ui, 'Segoe UI', Helvetica, Arial, sans-serif"

_W = 640          # logical viewBox width
_PAD_L = 56       # left padding (y-axis labels)
_PAD_R = 20
_PAD_T = 44       # top (title)
_PAD_B = 56       # bottom (x-axis labels)

_ICON_PATHS = None  # lazy {name: [width, pathdata]} from the vendored FA json


def _mix(hex_c, hex_b, t):
    """Linear blend of two #rrggbb colors (t=0 → c, t=1 → b)."""
    try:
        a = hex_c.lstrip("#"); b = hex_b.lstrip("#")
        ar, ag, ab = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
        br, bg, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
        return "#%02x%02x%02x" % (round(ar + (br - ar) * t),
                                  round(ag + (bg - ag) * t),
                                  round(ab + (bb - ab) * t))
    except Exception:
        return hex_c


def _icon_path(name):
    """[width, pathdata] for a FA solid icon name ('rocket' / 'fa-rocket'),
    or None. Loaded once from web/vendor/fontawesome/solid-paths.json."""
    global _ICON_PATHS
    if _ICON_PATHS is None:
        try:
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            with open(os.path.join(root, "web", "vendor", "fontawesome",
                                   "solid-paths.json"), encoding="utf-8") as f:
                _ICON_PATHS = json.load(f)
        except Exception:
            _ICON_PATHS = {}
    if not name:
        return None
    key = str(name).strip().lower()
    if key.startswith("fa-"):
        key = key[3:]
    return _ICON_PATHS.get(key)


def _icon_svg(name, cx, cy, size, fill):
    """<path> for icon `name` centered on (cx, cy) at `size` px height, or ''."""
    ip = _icon_path(name)
    if not ip:
        return ""
    w, d = ip
    scale = size / 512.0
    tx = cx - (w * scale) / 2
    ty = cy - size / 2
    return (f'<path transform="translate({tx:.1f} {ty:.1f}) scale({scale:.5f})" '
            f'd="{d}" fill="{fill}"/>')


def _coerce_data(spec):
    """Return [(label:str, value:float|None, icon:str)] from a spec. A row with
    no usable value is kept with value=None (the `steps` type doesn't need
    numbers); the numeric chart types filter those out in render_chart_svg.
    None if there's nothing chartable."""
    rows = []
    for d in (spec.get("data") or []):
        if not isinstance(d, dict):
            continue
        label = str(d.get("label", "")).strip()
        try:
            value = float(d.get("value"))
            if not math.isfinite(value):
                value = None
        except (TypeError, ValueError):
            value = None
        if not label and value is None:
            continue
        rows.append((label, value, str(d.get("icon", "") or "").strip()))
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


def _defs(rows, prefix, angle="vert"):
    """Shared <defs>: soft drop-shadow + one gradient per row (color → lighter).
    Gradient ids are '{prefix}{i}'."""
    if angle == "vert":
        coords = 'x1="0" y1="0" x2="0" y2="1"'
    else:
        coords = 'x1="0" y1="0" x2="1" y2="1"'
    g = [f'<filter id="{prefix}sh" x="-20%" y="-20%" width="140%" height="140%">'
         f'<feDropShadow dx="0" dy="2.5" stdDeviation="4" flood-color="#1a1817" '
         f'flood-opacity="0.22"/></filter>']
    for i in range(len(rows)):
        c = _PALETTE[i % len(_PALETTE)]
        g.append(f'<linearGradient id="{prefix}{i}" {coords}>'
                 f'<stop offset="0%" stop-color="{_mix(c, "#ffffff", 0.30)}"/>'
                 f'<stop offset="100%" stop-color="{c}"/></linearGradient>')
    return "<defs>" + "".join(g) + "</defs>"


def _title_svg(title, w):
    if not title:
        return ""
    return (f'<text x="{w/2:.0f}" y="27" text-anchor="middle" '
            f'font-size="17" font-weight="700" fill="{_TEXT}" '
            f'font-family="{_SERIF}">{html.escape(title)}</text>')


def _fmt_num(v):
    if v == int(v):
        return f"{int(v):,}"
    return f"{v:,.1f}"


def _wrap_svg(parts, h):
    return (f'<svg viewBox="0 0 {_W} {h}" xmlns="http://www.w3.org/2000/svg" '
            f'role="img" style="width:100%;height:auto" '
            f'font-family="{_SANS}">' + "".join(parts) + "</svg>")


def _bar_chart(rows, title):
    n = len(rows)
    h = 360
    plot_w = _W - _PAD_L - _PAD_R
    plot_h = h - _PAD_T - _PAD_B
    vmax = _nice_ceil(max(v for _, v, _ in rows))
    gap = plot_w / n
    bar_w = min(gap * 0.58, 76)
    parts = [_defs(rows, "bg_"), _title_svg(title, _W)]
    # Y grid (dotted) + labels
    for i in range(5):
        gv = vmax * i / 4
        y = _PAD_T + plot_h - (gv / vmax) * plot_h
        parts.append(f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_W-_PAD_R}" y2="{y:.1f}" '
                     f'stroke="{_GRID}" stroke-width="1" stroke-dasharray="2 5"/>')
        parts.append(f'<text x="{_PAD_L-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="{_MUTED}">{_fmt_num(gv)}</text>')
    # Bars — rounded tops, gradient fill, soft shadow.
    for i, (label, value, _icon) in enumerate(rows):
        bh = max(2.0, (value / vmax) * plot_h)
        x = _PAD_L + gap * i + (gap - bar_w) / 2
        y = _PAD_T + plot_h - bh
        r = min(7, bar_w / 2, bh)
        parts.append(
            f'<path d="M {x:.1f} {y+bh:.1f} L {x:.1f} {y+r:.1f} '
            f'Q {x:.1f} {y:.1f} {x+r:.1f} {y:.1f} L {x+bar_w-r:.1f} {y:.1f} '
            f'Q {x+bar_w:.1f} {y:.1f} {x+bar_w:.1f} {y+r:.1f} L {x+bar_w:.1f} {y+bh:.1f} Z" '
            f'fill="url(#bg_{i})" filter="url(#bg_sh)"/>')
        color = _PALETTE[i % len(_PALETTE)]
        parts.append(f'<text x="{x+bar_w/2:.1f}" y="{y-8:.1f}" text-anchor="middle" '
                     f'font-size="12" font-weight="700" fill="{color}">{_fmt_num(value)}</text>')
        parts.append(f'<text x="{_PAD_L+gap*i+gap/2:.1f}" y="{h-_PAD_B+20:.0f}" '
                     f'text-anchor="middle" font-size="11" fill="{_MUTED}">'
                     f'{html.escape(label[:16])}</text>')
    return _wrap_svg(parts, h)


def _line_chart(rows, title):
    h = 360
    plot_w = _W - _PAD_L - _PAD_R
    plot_h = h - _PAD_T - _PAD_B
    vmax = _nice_ceil(max(v for _, v, _ in rows))
    n = len(rows)
    step = plot_w / max(1, n - 1)
    c = _PALETTE[0]
    parts = [f'<defs><linearGradient id="lg_area" x1="0" y1="0" x2="0" y2="1">'
             f'<stop offset="0%" stop-color="{c}" stop-opacity="0.30"/>'
             f'<stop offset="100%" stop-color="{c}" stop-opacity="0"/></linearGradient>'
             f'<filter id="lg_sh" x="-20%" y="-20%" width="140%" height="140%">'
             f'<feDropShadow dx="0" dy="1.5" stdDeviation="2" flood-color="#1a1817" '
             f'flood-opacity="0.25"/></filter></defs>',
             _title_svg(title, _W)]
    for i in range(5):
        gv = vmax * i / 4
        y = _PAD_T + plot_h - (gv / vmax) * plot_h
        parts.append(f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_W-_PAD_R}" y2="{y:.1f}" '
                     f'stroke="{_GRID}" stroke-width="1" stroke-dasharray="2 5"/>')
        parts.append(f'<text x="{_PAD_L-8}" y="{y+4:.1f}" text-anchor="end" '
                     f'font-size="11" fill="{_MUTED}">{_fmt_num(gv)}</text>')
    pts = []
    for i, (label, value, _icon) in enumerate(rows):
        x = _PAD_L + step * i
        y = _PAD_T + plot_h - (value / vmax) * plot_h
        pts.append((x, y))
        parts.append(f'<text x="{x:.1f}" y="{h-_PAD_B+20:.0f}" text-anchor="middle" '
                     f'font-size="11" fill="{_MUTED}">{html.escape(label[:16])}</text>')
    # Smooth path (horizontal-tangent beziers between neighbours).
    d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        mx = (x0 + x1) / 2
        d += f" C {mx:.1f} {y0:.1f} {mx:.1f} {y1:.1f} {x1:.1f} {y1:.1f}"
    base_y = _PAD_T + plot_h
    parts.append(f'<path d="{d} L {pts[-1][0]:.1f} {base_y:.1f} '
                 f'L {pts[0][0]:.1f} {base_y:.1f} Z" fill="url(#lg_area)"/>')
    parts.append(f'<path d="{d}" fill="none" stroke="{c}" stroke-width="2.5" '
                 f'stroke-linejoin="round" stroke-linecap="round" filter="url(#lg_sh)"/>')
    for x, y in pts:
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="#ffffff" '
                     f'stroke="{c}" stroke-width="2.5"/>')
    # Last-value chip.
    lx, ly = pts[-1]
    val = _fmt_num(rows[-1][1])
    cw = 16 + 7.5 * len(val)
    cy_ = ly - 26 if ly - 26 > _PAD_T else ly + 18
    cx_ = min(lx - cw / 2, _W - _PAD_R - cw)
    parts.append(f'<rect x="{cx_:.1f}" y="{cy_:.1f}" width="{cw:.1f}" height="20" rx="10" '
                 f'fill="{c}" filter="url(#lg_sh)"/>')
    parts.append(f'<text x="{cx_+cw/2:.1f}" y="{cy_+14:.1f}" text-anchor="middle" '
                 f'font-size="12" font-weight="700" fill="#ffffff">{val}</text>')
    return _wrap_svg(parts, h)


def _wrap_center_text(title, cx, cy, max_chars=14, max_lines=3):
    """Word-wrapped medallion title as centered <text> lines."""
    words = title.split()
    lines, cur = [], ""
    for w in words:
        if len(cur) + len(w) + 1 <= max_chars or not cur:
            cur = (cur + " " + w).strip()
        else:
            lines.append(cur)
            cur = w
        if len(lines) == max_lines:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if not lines:
        return ""
    lh = 18
    y0 = cy - (len(lines) - 1) * lh / 2
    out = []
    for i, ln in enumerate(lines):
        out.append(f'<text x="{cx:.0f}" y="{y0 + i*lh + 5:.1f}" text-anchor="middle" '
                   f'font-size="15" font-weight="700" fill="{_TEXT}" '
                   f'font-family="{_SERIF}">{html.escape(ln[:max_chars+4])}</text>')
    return "".join(out)


def _pie_chart(rows, title):
    """Infographic donut: gradient ring slices with white gaps + soft shadow,
    white center medallion (title inside), icon-badge callouts with label +
    value around the ring, %-labels on the slices."""
    h = 430
    rows = [r for r in rows if r[1] > 0]
    total = sum(v for _, v, _ in rows)
    if total <= 0 or not rows:
        return None
    cx, cy = _W / 2, _PAD_T / 2 + h / 2
    ro, ri = 128, 76
    parts = [_defs(rows, "pg_", angle="diag")]
    # Ring (grouped so the shadow hugs the outer silhouette only).
    ring = []
    ang = -math.pi / 2
    mids = []
    for i, (label, value, icon) in enumerate(rows):
        frac = value / total
        a2 = ang + frac * 2 * math.pi
        # Guard: a single 100% slice needs two arcs; nudge the end angle.
        sweep = min(a2 - ang, 2 * math.pi - 1e-4)
        a2 = ang + sweep
        x1o, y1o = cx + ro * math.cos(ang), cy + ro * math.sin(ang)
        x2o, y2o = cx + ro * math.cos(a2), cy + ro * math.sin(a2)
        x1i, y1i = cx + ri * math.cos(ang), cy + ri * math.sin(ang)
        x2i, y2i = cx + ri * math.cos(a2), cy + ri * math.sin(a2)
        large = 1 if sweep > math.pi else 0
        ring.append(f'<path d="M {x1o:.1f} {y1o:.1f} A {ro} {ro} 0 {large} 1 '
                    f'{x2o:.1f} {y2o:.1f} L {x2i:.1f} {y2i:.1f} '
                    f'A {ri} {ri} 0 {large} 0 {x1i:.1f} {y1i:.1f} Z" '
                    f'fill="url(#pg_{i})" stroke="#ffffff" stroke-width="2.5" '
                    f'stroke-linejoin="round"/>')
        mids.append((ang + a2) / 2)
        ang = a2
    parts.append(f'<g filter="url(#pg_sh)">' + "".join(ring) + "</g>")
    # %-labels on the slices (halo for readability on light slices).
    for i, (label, value, icon) in enumerate(rows):
        frac = value / total
        if frac < 0.05:
            continue
        rm = (ro + ri) / 2
        px, py = cx + rm * math.cos(mids[i]), cy + rm * math.sin(mids[i])
        parts.append(f'<text x="{px:.1f}" y="{py+5:.1f}" text-anchor="middle" '
                     f'font-size="14" font-weight="700" fill="#ffffff" '
                     f'style="paint-order:stroke" stroke="rgba(26,24,23,0.45)" '
                     f'stroke-width="2.5" stroke-linejoin="round">{frac*100:.0f}%</text>')
    # Center medallion.
    parts.append(f'<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{ri-12}" fill="#ffffff" '
                 f'filter="url(#pg_sh)"/>')
    if title:
        parts.append(_wrap_center_text(title, cx, cy))
    else:
        parts.append(f'<text x="{cx:.0f}" y="{cy:.0f}" text-anchor="middle" '
                     f'font-size="20" font-weight="700" fill="{_TEXT}">{_fmt_num(total)}</text>')
        parts.append(f'<text x="{cx:.0f}" y="{cy+20:.0f}" text-anchor="middle" '
                     f'font-size="11" fill="{_MUTED}">Gesamt</text>')
    # Callouts: icon badge + label + value at each slice's mid-angle, collision-
    # relaxed per side (left/right sorted by y, minimum spacing pushed apart).
    slots = {"l": [], "r": []}
    for i, (label, value, icon) in enumerate(rows):
        bx = cx + (ro + 30) * math.cos(mids[i])
        by = cy + (ro + 30) * math.sin(mids[i])
        side = "r" if math.cos(mids[i]) >= 0 else "l"
        slots[side].append([by, i, bx])
    for side in ("l", "r"):
        entries = sorted(slots[side])
        for j in range(1, len(entries)):
            if entries[j][0] - entries[j-1][0] < 40:
                entries[j][0] = entries[j-1][0] + 40
        for by, i, bx in entries:
            label, value, icon = rows[i]
            color = _PALETTE[i % len(_PALETTE)]
            ex, ey = cx + ro * math.cos(mids[i]), cy + ro * math.sin(mids[i])
            parts.append(f'<line x1="{ex:.1f}" y1="{ey:.1f}" x2="{bx:.1f}" y2="{by:.1f}" '
                         f'stroke="{color}" stroke-width="1.2" stroke-dasharray="1 3"/>')
            parts.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="15" fill="{color}" '
                         f'filter="url(#pg_sh)"/>')
            ic = _icon_svg(icon, bx, by, 15, "#ffffff") if icon else ""
            parts.append(ic or f'<text x="{bx:.1f}" y="{by+4.5:.1f}" text-anchor="middle" '
                               f'font-size="12" font-weight="700" fill="#ffffff">{i+1}</text>')
            anchor = "start" if side == "r" else "end"
            tx = bx + 22 if side == "r" else bx - 22
            parts.append(f'<text x="{tx:.1f}" y="{by-1:.1f}" text-anchor="{anchor}" '
                         f'font-size="12" font-weight="600" fill="{_TEXT}">'
                         f'{html.escape(label[:20])}</text>')
            parts.append(f'<text x="{tx:.1f}" y="{by+14:.1f}" text-anchor="{anchor}" '
                         f'font-size="11" fill="{_MUTED}">{_fmt_num(value)}</text>')
    if title:
        # Title lives in the medallion; no top headline needed.
        pass
    return _wrap_svg(parts, h)


def _funnel_chart(rows, title):
    """Infographic funnel: centered gradient trapezoids (widest first row),
    soft shadow, value inside each segment, icon badge + label on the left."""
    n = len(rows)
    seg_h, gap = 52, 10
    h = _PAD_T + n * (seg_h + gap) + 24
    vmax = max(v for _, v, _ in rows)
    if vmax <= 0:
        return None
    cx = _W * 0.58
    max_w, min_w = 340, 110
    widths = [min_w + (max_w - min_w) * (v / vmax) for _, v, _ in rows]
    parts = [_defs(rows, "fg_", angle="diag"), _title_svg(title, _W)]
    for i, (label, value, icon) in enumerate(rows):
        y = _PAD_T + 8 + i * (seg_h + gap)
        w_top = widths[i]
        w_bot = widths[i + 1] if i + 1 < n else widths[i] * 0.72
        w_bot = min(w_bot, w_top)  # never widen downwards
        color = _PALETTE[i % len(_PALETTE)]
        parts.append(f'<path d="M {cx-w_top/2:.1f} {y:.1f} L {cx+w_top/2:.1f} {y:.1f} '
                     f'L {cx+w_bot/2:.1f} {y+seg_h:.1f} L {cx-w_bot/2:.1f} {y+seg_h:.1f} Z" '
                     f'fill="url(#fg_{i})" filter="url(#fg_sh)"/>')
        parts.append(f'<text x="{cx:.1f}" y="{y+seg_h/2+5:.1f}" text-anchor="middle" '
                     f'font-size="14" font-weight="700" fill="#ffffff" '
                     f'style="paint-order:stroke" stroke="rgba(26,24,23,0.35)" '
                     f'stroke-width="2" stroke-linejoin="round">{_fmt_num(value)}</text>')
        # Left rail: icon badge + label, dotted leader to the segment.
        bx, by = 96, y + seg_h / 2
        parts.append(f'<line x1="{bx+18:.1f}" y1="{by:.1f}" x2="{cx-w_top/2-6:.1f}" '
                     f'y2="{by:.1f}" stroke="{color}" stroke-width="1.2" '
                     f'stroke-dasharray="1 3"/>')
        parts.append(f'<circle cx="{bx:.1f}" cy="{by:.1f}" r="15" fill="{color}" '
                     f'filter="url(#fg_sh)"/>')
        ic = _icon_svg(icon, bx, by, 15, "#ffffff") if icon else ""
        parts.append(ic or f'<text x="{bx:.1f}" y="{by+4.5:.1f}" text-anchor="middle" '
                           f'font-size="12" font-weight="700" fill="#ffffff">{i+1}</text>')
        parts.append(f'<text x="{bx-22:.1f}" y="{by+4:.1f}" text-anchor="end" '
                     f'font-size="12" font-weight="600" fill="{_TEXT}">'
                     f'{html.escape(label[:14])}</text>')
        # Right: share of the first (top) row.
        share = rows[i][1] / rows[0][1] * 100 if rows[0][1] else 0
        parts.append(f'<text x="{cx+max_w/2+22:.1f}" y="{by+4:.1f}" text-anchor="start" '
                     f'font-size="12" font-weight="700" fill="{color}">{share:.0f}%</text>')
    return _wrap_svg(parts, h)


def _steps_chart(rows, title):
    """Infographic step list: one numbered card per row — colored number box,
    bold label, optional value on the right and icon badge. Values are optional
    display data here (unlike the other types the geometry doesn't scale)."""
    n = len(rows)
    card_h, gap = 56, 12
    h = _PAD_T + n * (card_h + gap) + 8
    x0, card_w = 60, _W - 120
    parts = [_defs(rows, "sg_", angle="diag"), _title_svg(title, _W)]
    for i, (label, value, icon) in enumerate(rows):
        y = _PAD_T + 4 + i * (card_h + gap)
        color = _PALETTE[i % len(_PALETTE)]
        parts.append(f'<rect x="{x0}" y="{y:.1f}" width="{card_w}" height="{card_h}" '
                     f'rx="10" fill="#ffffff" stroke="{_mix(color, "#ffffff", 0.45)}" '
                     f'stroke-width="1.5" filter="url(#sg_sh)"/>')
        parts.append(f'<rect x="{x0}" y="{y:.1f}" width="{card_h}" height="{card_h}" '
                     f'rx="10" fill="url(#sg_{i})"/>')
        parts.append(f'<text x="{x0+card_h/2:.1f}" y="{y+card_h/2+8:.1f}" '
                     f'text-anchor="middle" font-size="22" font-weight="800" '
                     f'fill="#ffffff">{i+1:02d}</text>')
        parts.append(f'<text x="{x0+card_h+18:.1f}" y="{y+card_h/2+5:.1f}" '
                     f'font-size="14" font-weight="700" fill="{_TEXT}">'
                     f'{html.escape(label[:42])}</text>')
        right_x = x0 + card_w - 18
        if icon and _icon_path(icon):
            parts.append(f'<circle cx="{right_x-8:.1f}" cy="{y+card_h/2:.1f}" r="14" '
                         f'fill="{_mix(color, "#ffffff", 0.85)}"/>')
            parts.append(_icon_svg(icon, right_x - 8, y + card_h / 2, 14, color))
            right_x -= 34
        if value:
            parts.append(f'<text x="{right_x:.1f}" y="{y+card_h/2+5:.1f}" '
                         f'text-anchor="end" font-size="13" font-weight="700" '
                         f'fill="{color}">{_fmt_num(value)}</text>')
    return _wrap_svg(parts, h)


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
        if ctype == "steps":
            return _steps_chart(rows, title)
        rows = [r for r in rows if r[1] is not None]  # numeric types need values
        if not rows:
            return None
        if ctype == "line":
            return _line_chart(rows, title)
        if ctype in ("pie", "donut"):
            return _pie_chart(rows, title)
        if ctype == "funnel":
            return _funnel_chart(rows, title)
        return _bar_chart(rows, title)  # default
    except Exception:
        return None
