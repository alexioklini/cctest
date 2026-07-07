"""Deterministic local OCR toolset — ocr_inspect / ocr_extract / ocr_region /
ocr_fields / ocr_tables.

WHY: attaching a scan/photo/receipt used to give the model only two options —
"look" at the image natively (multimodal) or a free-text VISION DESCRIPTION of
it. Both are the model reading pixels and re-typing numbers: it misreads a
Rechnungsbetrag and moves on. Like the xlsx toolset (model supplies a SQL
SELECT / a JSON spec, the SERVER moves the data deterministically), these tools
run Tesseract LOCALLY — no LLM in the loop, no cloud call — and hand back
text-faithful output plus per-word confidences the model can reason about.

Engine: pytesseract → the `tesseract` binary (5.x), PIL for images, PyMuPDF
(fitz) to rasterise PDF pages. ONE shared primitive — `_page_tsv()` runs
`image_to_data` per page and everything (plain text, layout, tables, regions,
field regex) is derived from that word/geometry/confidence table. This is
DISTINCT from doc_convert's `_extract_with_local_vision` (a Vision-LLM) and
`_extract_with_mistral_ocr` (cloud) — those stay wired only into the PDF
extraction fallback; the OCR TOOLS never call an LLM.

Wired per the 4-site rule (TOOL_DEFINITIONS / TOOL_GROUPS "ocr" / impl here /
TOOL_DISPATCH). Reaches brain runtime via lazy `import brain as _brain`.
"""

from __future__ import annotations

import csv
import io
import os
import re

from engine.context import get_request_context
from engine.tool_exec import _ok, _err

# Rasterisation + result caps. DPI 300 is the sweet spot for printed-text OCR
# (higher barely helps, costs memory); we cap pages/image size so a huge PDF
# can't wedge a chat turn. Preview text is capped — full text goes to an
# artifact via out=.
_RENDER_DPI = 300
_MAX_PAGES = 50
_MAX_IMAGE_MB = 40
_PREVIEW_CHARS = 6000
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif"}
_PDF_EXTS = {".pdf"}
_DEFAULT_LANG = "deu+eng"


# ---------------------------------------------------------------------------
# Engine availability + input resolution
# ---------------------------------------------------------------------------
def _require_tesseract():
    """Return the pytesseract module, or an (_err) string if the engine or its
    binary is missing. Fail LOUD with the install hint — silent degradation to
    "no text" would look like a blank document."""
    try:
        import pytesseract  # noqa
    except ImportError:
        return None, _err(
            "OCR engine not available: the `pytesseract` Python package is not "
            "installed. Install it (pip install pytesseract) plus the tesseract "
            "binary (brew install tesseract tesseract-lang).")
    try:
        pytesseract.get_tesseract_version()
    except Exception:
        return None, _err(
            "OCR engine not available: the `tesseract` binary was not found on "
            "PATH. Install it with `brew install tesseract tesseract-lang` "
            "(the tesseract-lang formula adds the German/other language data).")
    return pytesseract, None


def _resolve_input(path: str, tool: str):
    """Map a model-supplied path to an absolute, readable file. Accepts an
    absolute path or one relative to cwd / the artifact folder. Returns
    (abs_path, error_or_None)."""
    if not path or not isinstance(path, str):
        return None, _err(f"{tool}: 'path' (a scan image or PDF) is required.")
    cand = os.path.expanduser(path)
    tries = [cand] if os.path.isabs(cand) else [
        os.path.abspath(cand),
    ]
    # Also try the session artifact folder for a bare filename.
    if not os.path.isabs(cand):
        try:
            from engine.tools.file_tools import _resolve_artifact_dir
            adir, _ = _resolve_artifact_dir()
            if adir:
                tries.append(os.path.join(adir, cand))
        except Exception:
            pass
    for t in tries:
        if os.path.isfile(t):
            ext = os.path.splitext(t)[1].lower()
            if ext not in _IMAGE_EXTS and ext not in _PDF_EXTS:
                return None, _err(
                    f"{tool}: unsupported file type '{ext}'. OCR handles images "
                    f"({', '.join(sorted(_IMAGE_EXTS))}) and .pdf.")
            mb = os.path.getsize(t) / (1024 * 1024)
            if mb > _MAX_IMAGE_MB:
                return None, _err(
                    f"{tool}: file too large ({mb:.1f} MB > {_MAX_IMAGE_MB} MB).")
            return t, None
    return None, _err(f"{tool}: file not found: {path}")


def _parse_pages(spec, n_pages: int) -> list[int]:
    """'1-3,5' → [0,1,2,4] (0-based, clamped to the doc). Empty → all pages
    (capped at _MAX_PAGES)."""
    if not spec:
        return list(range(min(n_pages, _MAX_PAGES)))
    out: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                continue
            for i in range(lo, hi + 1):
                if 1 <= i <= n_pages:
                    out.add(i - 1)
        else:
            try:
                i = int(part)
            except ValueError:
                continue
            if 1 <= i <= n_pages:
                out.add(i - 1)
    return sorted(out)[:_MAX_PAGES]


# ---------------------------------------------------------------------------
# Rasterisation → PIL pages
# ---------------------------------------------------------------------------
def _load_pages(path: str, pages_spec):
    """Yield (page_index, PIL.Image) for the requested pages. Images are a
    single page (index 0). PDF pages are rendered at _RENDER_DPI via fitz."""
    from PIL import Image
    ext = os.path.splitext(path)[1].lower()
    if ext in _IMAGE_EXTS:
        img = Image.open(path)
        img.load()
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return [(0, img)], 1
    # PDF → rasterise each requested page
    import fitz
    doc = fitz.open(path)
    n = doc.page_count
    idxs = _parse_pages(pages_spec, n)
    zoom = _RENDER_DPI / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    out = []
    for i in idxs:
        pix = doc.load_page(i).get_pixmap(matrix=matrix, alpha=False)
        out.append((i, Image.open(io.BytesIO(pix.tobytes("png")))))
    doc.close()
    return out, n


# ---------------------------------------------------------------------------
# The single OCR primitive: one page → word rows with geometry + confidence
# ---------------------------------------------------------------------------
def _page_tsv(pyt, img, lang: str) -> list[dict]:
    """Run tesseract image_to_data on one page. Returns a list of word dicts
    {text, conf, left, top, width, height, block, par, line, word} for words
    with non-empty text and conf >= 0 (tesseract emits -1 conf structural
    rows). This ONE table backs plain text, layout, tables and fields."""
    from pytesseract import Output
    data = pyt.image_to_data(img, lang=lang, output_type=Output.DICT)
    words = []
    for i in range(len(data["text"])):
        txt = (data["text"][i] or "").strip()
        try:
            conf = float(data["conf"][i])
        except (ValueError, TypeError):
            conf = -1.0
        if not txt or conf < 0:
            continue
        words.append({
            "text": txt, "conf": conf,
            "left": int(data["left"][i]), "top": int(data["top"][i]),
            "width": int(data["width"][i]), "height": int(data["height"][i]),
            "block": int(data["block_num"][i]), "par": int(data["par_num"][i]),
            "line": int(data["line_num"][i]), "word": int(data["word_num"][i]),
        })
    return words


def _words_to_text(words: list[dict]) -> str:
    """Reconstruct reading-order text: group by (block, par, line), join words
    with spaces, lines with newlines, blocks with blank lines."""
    if not words:
        return ""
    lines: dict = {}
    for w in words:
        lines.setdefault((w["block"], w["par"], w["line"]), []).append(w)
    out_lines = []
    prev_block = None
    for key in sorted(lines.keys()):
        if prev_block is not None and key[0] != prev_block:
            out_lines.append("")  # blank line between blocks
        row = sorted(lines[key], key=lambda w: w["left"])
        out_lines.append(" ".join(w["text"] for w in row))
        prev_block = key[0]
    return "\n".join(out_lines)


def _mean_conf(words: list[dict]) -> float:
    return round(sum(w["conf"] for w in words) / len(words), 1) if words else 0.0


def _ocr_all_pages(pyt, path: str, pages_spec, lang: str):
    """OCR every requested page. Returns (list of {page, text, words,
    mean_conf}, total_page_count)."""
    pages, total = _load_pages(path, pages_spec)
    results = []
    for idx, img in pages:
        words = _page_tsv(pyt, img, lang)
        results.append({
            "page": idx + 1, "text": _words_to_text(words),
            "words": words, "mean_conf": _mean_conf(words),
            "img_size": img.size,
        })
    return results, total


def _anon(text: str, src: str) -> str:
    """GDPR-anonymise a tool result, mirroring the xlsx tools."""
    try:
        import brain as _brain
        return _brain._gdpr_anon_tool_text(text, src)
    except Exception:
        return text


def _write_artifact(out_name: str, tool: str, content: str):
    """Write full output to the session artifact folder. Returns (rel_or_abs
    path string, error_or_None)."""
    from engine.tools.file_tools import _enforce_artifact_path
    out_path, perr = _enforce_artifact_path(out_name, tool)
    if perr:
        return None, perr
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    try:
        from engine.tools.file_tools import _after_file_write
        _after_file_write(out_path)
    except Exception:
        pass
    return out_path, None


def _log_pages(pages: int):
    """Record OCR page count in the cost ledger ($0 — local engine — but the
    page count is an audit signal, exactly like the cloud OCR path)."""
    try:
        from engine.doc_convert import _log_ocr_cost
        _log_ocr_cost(model="tesseract", provider="local", pages=pages,
                      cost_usd=0.0)
    except Exception:
        pass


# ===========================================================================
# TOOLS
# ===========================================================================
def tool_ocr_inspect(args: dict) -> str:
    """Profile a scan/PDF WITHOUT running full OCR: page count, image size,
    detected orientation/script (tesseract OSD), and a rough text-density
    hint. The "call this first" tool, analogous to xlsx_inspect."""
    pyt, err = _require_tesseract()
    if err:
        return err
    path, err = _resolve_input(args.get("path"), "ocr_inspect")
    if err:
        return err
    lang = args.get("lang") or _DEFAULT_LANG
    try:
        pages, total = _load_pages(path, args.get("pages") or "1")
        lines = [f"File: {os.path.basename(path)}", f"Pages: {total}"]
        for idx, img in pages[:3]:
            info = [f"  Page {idx + 1}: {img.size[0]}x{img.size[1]} px"]
            try:
                from pytesseract import Output
                osd = pyt.image_to_osd(img, output_type=Output.DICT)
                info.append(f"rotate={osd.get('rotate', 0)}°")
                info.append(f"script={osd.get('script', '?')}")
            except Exception:
                pass  # OSD fails on sparse/blank pages — not fatal
            # cheap density probe on page 1
            words = _page_tsv(pyt, img, lang)
            info.append(f"words≈{len(words)}")
            info.append(f"mean_conf={_mean_conf(words)}")
            lines.append(" · ".join(info))
        report = "\n".join(lines)
        return _ok({"pages": total,
                    "report": _anon(report, f"ocr_inspect:{path}")})
    except Exception as e:
        return _err(f"ocr_inspect failed: {e}")


def tool_ocr_extract(args: dict) -> str:
    """Deterministic full-text OCR of an image or PDF. mode: 'text' (plain
    reading-order), 'layout' (blank line between blocks, preserves paragraph
    breaks) or 'markdown' (same as layout with page headers). Preview capped;
    out='name.txt' saves the full text as an artifact."""
    pyt, err = _require_tesseract()
    if err:
        return err
    path, err = _resolve_input(args.get("path"), "ocr_extract")
    if err:
        return err
    lang = args.get("lang") or _DEFAULT_LANG
    mode = (args.get("mode") or "text").lower()
    try:
        results, total = _ocr_all_pages(pyt, path, args.get("pages"), lang)
        if not results:
            return _err("ocr_extract: no pages matched (check the pages= range).")
        _log_pages(len(results))
        chunks = []
        for r in results:
            body = r["text"]
            if mode in ("markdown", "layout") and len(results) > 1:
                chunks.append(f"## Page {r['page']}\n\n{body}")
            else:
                chunks.append(body)
        full = ("\n\n".join(chunks)).strip()
        mean = round(sum(r["mean_conf"] for r in results) / len(results), 1)

        saved = None
        out_name = args.get("out")
        if out_name:
            out_path, perr = _write_artifact(out_name, "ocr_extract", full)
            if perr:
                return perr
            saved = os.path.basename(out_path)

        preview = full
        truncated = False
        if len(preview) > _PREVIEW_CHARS:
            preview = preview[:_PREVIEW_CHARS]
            truncated = True

        return _ok({
            "pages": len(results),
            "mean_confidence": mean,
            "lang": lang,
            "chars": len(full),
            "truncated": truncated,
            "saved_to": saved,
            "text": _anon(preview, f"ocr_extract:{path}"),
            "note": (f"preview capped at {_PREVIEW_CHARS} chars — "
                     f"full text in the artifact" if truncated and saved else
                     f"preview capped at {_PREVIEW_CHARS} of {len(full)} chars — "
                     f"pass out='text.txt' for the full extract" if truncated else None),
        })
    except Exception as e:
        return _err(f"ocr_extract failed: {e}")


def tool_ocr_region(args: dict) -> str:
    """OCR only a rectangular region of a page — for 'just the stamp top-right'
    or 'only the footer'. bbox=[x,y,w,h] in pixels (unit='px', default) or
    percent of page size (unit='pct'). Single page (page=1 default)."""
    pyt, err = _require_tesseract()
    if err:
        return err
    path, err = _resolve_input(args.get("path"), "ocr_region")
    if err:
        return err
    bbox = args.get("bbox")
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        return _err("ocr_region: bbox=[x, y, width, height] is required.")
    lang = args.get("lang") or _DEFAULT_LANG
    unit = (args.get("unit") or "px").lower()
    page_no = int(args.get("page") or 1)
    try:
        pages, _ = _load_pages(path, str(page_no))
        if not pages:
            return _err(f"ocr_region: page {page_no} not found.")
        _, img = pages[0]
        W, H = img.size
        x, y, w, h = [float(v) for v in bbox]
        if unit in ("pct", "percent", "%"):
            x, w = x / 100.0 * W, w / 100.0 * W
            y, h = y / 100.0 * H, h / 100.0 * H
        left, top = max(0, int(x)), max(0, int(y))
        right, bottom = min(W, int(x + w)), min(H, int(y + h))
        if right <= left or bottom <= top:
            return _err(f"ocr_region: empty crop after clamping to page {W}x{H}.")
        crop = img.crop((left, top, right, bottom))
        words = _page_tsv(pyt, crop, lang)
        _log_pages(1)
        text = _words_to_text(words)
        return _ok({
            "page": page_no,
            "bbox_px": [left, top, right - left, bottom - top],
            "mean_confidence": _mean_conf(words),
            "text": _anon(text, f"ocr_region:{path}"),
        })
    except Exception as e:
        return _err(f"ocr_region failed: {e}")


def tool_ocr_fields(args: dict) -> str:
    """Deterministic structured extraction: OCR the document, then apply a
    per-field REGEX to the recognised text — no LLM guessing. Each field is
    {name, pattern} where pattern is a Python regex with ONE capture group (or
    the whole match). Returns validated JSON {field: value|null}. For invoices,
    forms, receipts: name+pattern like {'betrag', r'(\\d[\\d.,]*)\\s*EUR'}."""
    pyt, err = _require_tesseract()
    if err:
        return err
    path, err = _resolve_input(args.get("path"), "ocr_fields")
    if err:
        return err
    fields = args.get("fields")
    if not isinstance(fields, list) or not fields:
        return _err("ocr_fields: fields=[{name, pattern}, …] is required "
                    "(pattern = a regex; use one capture group for the value).")
    lang = args.get("lang") or _DEFAULT_LANG
    try:
        results, _ = _ocr_all_pages(pyt, path, args.get("pages"), lang)
        _log_pages(len(results))
        text = "\n".join(r["text"] for r in results)
        out = {}
        misses = []
        for f in fields:
            name = (f.get("name") or "").strip() if isinstance(f, dict) else ""
            pattern = f.get("pattern") if isinstance(f, dict) else None
            if not name or not pattern:
                continue
            flags = re.IGNORECASE | re.MULTILINE
            try:
                m = re.search(pattern, text, flags)
            except re.error as rex:
                out[name] = None
                misses.append(f"{name} (bad regex: {rex})")
                continue
            if m:
                out[name] = (m.group(1) if m.groups() else m.group(0)).strip()
            else:
                out[name] = None
                misses.append(name)
        for k, v in out.items():
            if v:
                out[k] = _anon(v, f"ocr_fields:{path}:{k}")
        return _ok({"fields": out, "unmatched": misses})
    except Exception as e:
        return _err(f"ocr_fields failed: {e}")


def tool_ocr_tables(args: dict) -> str:
    """Extract tabular data from a scan/PDF deterministically: OCR word
    geometry is clustered into columns (by x-position) and rows (by line), and
    emitted as CSV. out='name.csv' saves the full table as an artifact (which a
    follow-up xlsx_query/xlsx_inspect can then read). Best for gridded
    tables/statements; free-form text is better via ocr_extract."""
    pyt, err = _require_tesseract()
    if err:
        return err
    path, err = _resolve_input(args.get("path"), "ocr_tables")
    if err:
        return err
    lang = args.get("lang") or _DEFAULT_LANG
    try:
        results, _ = _ocr_all_pages(pyt, path, args.get("pages"), lang)
        _log_pages(len(results))
        all_rows = []
        for r in results:
            all_rows.extend(_words_to_rows(r["words"]))
        if not all_rows:
            return _err("ocr_tables: no table-like content recognised.")
        n_cols = max(len(row) for row in all_rows)
        norm = [row + [""] * (n_cols - len(row)) for row in all_rows]

        buf = io.StringIO()
        csv.writer(buf).writerows(norm)
        csv_text = buf.getvalue()

        saved = None
        out_name = args.get("out")
        if out_name:
            if not out_name.lower().endswith(".csv"):
                out_name += ".csv"
            out_path, perr = _write_artifact(out_name, "ocr_tables", csv_text)
            if perr:
                return perr
            saved = os.path.basename(out_path)

        preview_rows = norm[:30]
        pbuf = io.StringIO()
        csv.writer(pbuf).writerows(preview_rows)
        preview = pbuf.getvalue()
        if len(norm) > 30:
            preview += f"[… {len(norm) - 30} more rows]\n"
        return _ok({
            "rows": len(norm),
            "cols": n_cols,
            "saved_to": saved,
            "csv_preview": _anon(preview, f"ocr_tables:{path}"),
            "note": (f"full table saved — open it with xlsx_inspect/xlsx_query"
                     if saved else "pass out='table.csv' to save the full table"),
        })
    except Exception as e:
        return _err(f"ocr_tables failed: {e}")


def _words_to_rows(words: list[dict]) -> list[list[str]]:
    """Cluster OCR words into a grid. Rows = tesseract (block,par,line) groups
    ordered top→bottom; columns = x-position clusters shared across all rows
    (so cells align into columns). Deterministic, no ML."""
    if not words:
        return []
    # Column anchors: cluster all word left-edges with a gap threshold scaled
    # to median word height (font size proxy).
    heights = sorted(w["height"] for w in words)
    med_h = heights[len(heights) // 2] or 10
    gap = med_h * 1.5
    lefts = sorted(w["left"] for w in words)
    anchors = [lefts[0]]
    for l in lefts[1:]:
        if l - anchors[-1] > gap:
            anchors.append(l)

    def col_of(left: int) -> int:
        best, bi = None, 0
        for i, a in enumerate(anchors):
            d = abs(left - a)
            if best is None or d < best:
                best, bi = d, i
        return bi

    line_groups: dict = {}
    for w in words:
        line_groups.setdefault((w["block"], w["par"], w["line"]), []).append(w)

    def line_top(items):
        return min(w["top"] for w in items)

    rows = []
    for key in sorted(line_groups.keys(), key=lambda k: line_top(line_groups[k])):
        cells = [""] * len(anchors)
        for w in sorted(line_groups[key], key=lambda w: w["left"]):
            c = col_of(w["left"])
            cells[c] = (cells[c] + " " + w["text"]).strip()
        if any(cells):
            rows.append(cells)
    return rows
