"""Per-format file DE-anonymise walkers.

The forward pseudonymisation walkers (`pseudonymize_file`) were retired in
v9.x — pseudonymisation of attachment content now happens text-side, inside
`brain.tool_read_document` / `tool_read_file` via `_gdpr_anon_tool_text`.
This module only carries the reverse pass: when the LLM writes a file via
`write_file` / `edit_file`, `brain._after_file_write` calls
`deanonymize_file` to restore the real PII values into the artifact before
the user sees it.

Each walker rewalks one file format and calls `deanonymize_text` on each
text run / cell using the active mapping. The mapping's `forward` dict is
the source of truth — tokens that didn't survive the LLM round-trip
(mangled, overwritten) stay as tokens. `restored_count` is summed across
all runs.

Supported formats: `.docx`, `.pptx`, `.xlsx`, `.csv`, plus all plain-text
extensions in `_PLAIN_EXTS` (incl. `.svg`, which is text XML). Unsupported
types are copied src→dst unchanged (the LLM may emit a PNG we never
pseudonymised).
"""

from __future__ import annotations

import csv
import io
import os
import shutil
import zipfile
from typing import Callable
from xml.etree import ElementTree as ET

# Namespaces (cribbed from server_lib/translate/document.py — same OOXML
# parts, same tags).
DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PPTX_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
DOCX_TEXT_TAG = f"{{{DOCX_NS}}}t"
PPTX_TEXT_TAG = f"{{{PPTX_A_NS}}}t"

# Hard cap on a single run's text — same value used in translation. A pasted
# blob of 100K chars in one <w:t> is pathological; truncate to keep regex
# scan latency bounded.
MAX_RUN_CHARS = 64_000

# Extensions handled by this module. `pseudonymizer.pseudonymize_file`
# dispatches off this set.
SUPPORTED_EXTS = frozenset({
    ".docx", ".pptx", ".xlsx",
    ".txt", ".md", ".csv", ".json", ".log",
    ".html", ".htm", ".svg",
})

# Plain-text extensions — read/written as utf-8 strings without structural
# parsing. JSON is treated as plain text on purpose: PII inside a JSON
# value will be replaced and the JSON stays well-formed (tokens are
# JSON-string-safe — no quotes / backslashes). SVG is text XML and reverses
# the same way — a locally-rendered `render_diagram` .svg (M7/G6) now carries
# REAL values (render_diagram is in GDPR_ARGS_DEANON_TOOLS), and a model-
# written .svg carries fake tokens that the same string-replace restores;
# tokens are XML-text-safe (no `<`/`>`/`&`).
_PLAIN_EXTS = frozenset({".txt", ".md", ".log", ".html", ".htm", ".svg"})


class FilePseudonymizeError(Exception):
    """Raised when a walker can't process a file. The worker treats this as
    a recoverable error — emits `gdpr_recovery_required` and the user picks
    local-model or cancel. Never falls through to sending the original."""


# ---------------------------------------------------------------------------
# Scanner + replacement seam — late-bound to avoid an engine→brain cycle at
# import time. brain.py is the source of truth for `_pii_scan_text`; we don't
# import it eagerly because engine.* is loaded during brain.py module init.
# ---------------------------------------------------------------------------


def _reverse_text(text: str, mapping) -> tuple[str, int]:
    """De-anonymise one chunk. Returns `(restored_text, restored_count)`."""
    if not text:
        return text, 0
    from pseudonymizer import deanonymize_text
    return deanonymize_text(text, mapping=mapping)


# ---------------------------------------------------------------------------
# OOXML walker — shared between docx + pptx
# ---------------------------------------------------------------------------


def _office_targets_docx(zf: zipfile.ZipFile) -> list[str]:
    targets = []
    for n in sorted(zf.namelist()):
        if n == "word/document.xml":
            targets.append(n)
        elif (n.startswith("word/header") or n.startswith("word/footer")
              or n.startswith("word/footnotes") or n.startswith("word/endnotes")
              or n.startswith("word/comments")) and n.endswith(".xml"):
            targets.append(n)
    return targets


def _office_targets_pptx(zf: zipfile.ZipFile) -> list[str]:
    targets = []
    for n in sorted(zf.namelist()):
        if (n.startswith("ppt/slides/slide")
                or n.startswith("ppt/notesSlides/notesSlide")
                or n.startswith("ppt/slideLayouts/slideLayout")
                or n.startswith("ppt/slideMasters/slideMaster")) and n.endswith(".xml"):
            targets.append(n)
    return targets


def _walk_office(
    src_path: str, dst_path: str, *,
    text_tag: str,
    target_files_fn: Callable[[zipfile.ZipFile], list[str]],
    ns_uri: str,
    transform: Callable[[str], tuple[str, int]],
) -> tuple[int, int]:
    """Generic OOXML walker.

    `transform(text) -> (new_text, count)` is the per-run rewriter. `count`
    is added to a return total (used by deanonymise to report restored
    tokens; pseudonymise uses it for finding counts).

    Returns `(runs_visited, transform_count_total)`.
    """
    # Register the prefix so ET emits the canonical alias rather than ns0:.
    if ns_uri == DOCX_NS:
        ET.register_namespace("w", ns_uri)
    elif ns_uri == PPTX_A_NS:
        ET.register_namespace("a", ns_uri)

    parts: dict[str, ET.ElementTree] = {}
    runs_visited = 0
    total_count = 0

    with zipfile.ZipFile(src_path, "r") as zf:
        targets = target_files_fn(zf)
        for name in targets:
            data = zf.read(name)
            root = ET.fromstring(data)
            tree = ET.ElementTree(root)
            for el in root.iter(text_tag):
                txt = el.text or ""
                if not txt:
                    continue
                runs_visited += 1
                if len(txt) > MAX_RUN_CHARS:
                    # Don't silently drop — leave as-is. The scanner is regex
                    # based; a 64K cap on a single run is conservative.
                    continue
                new_text, count = transform(txt)
                total_count += count
                if new_text != txt:
                    el.text = new_text
                    # Preserve xml:space if leading/trailing whitespace.
                    if new_text and new_text != new_text.strip():
                        el.set("{http://www.w3.org/XML/1998/namespace}space",
                               "preserve")
            parts[name] = tree

    # Re-zip preserving member order so layout apps that key off it are happy.
    # CRITICAL: write to a TEMP file, never in-place. When src_path == dst_path
    # (the deanonymise-in-place callback calls `deanonymize_file(path, path)`),
    # opening `dst_path` in mode "w" TRUNCATES the source to 0 bytes before the
    # `zin.read(name)` loop has read its members — the read then fails with
    # "Truncated file header", the re-zip aborts mid-write, and the file is
    # left as a 22-byte empty-EOCD stub (a 47 KB .docx destroyed on write-back;
    # chat 3811cb61). Staging in a sibling temp + atomic os.replace keeps the
    # original readable for the whole copy and makes the swap all-or-nothing.
    _tmp_path = dst_path + ".pii-tmp"
    try:
        with zipfile.ZipFile(src_path, "r") as zin, zipfile.ZipFile(
                _tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in zin.namelist():
                if name in parts:
                    buf = io.BytesIO()
                    parts[name].write(buf, xml_declaration=True,
                                      encoding="UTF-8", default_namespace=None)
                    zout.writestr(name, buf.getvalue())
                else:
                    zout.writestr(name, zin.read(name))
        os.replace(_tmp_path, dst_path)
    except BaseException:
        # Never leave the staging file behind on failure (and never let a
        # partial temp masquerade as the output — the original dst is untouched
        # because os.replace only runs after a clean re-zip).
        try:
            if os.path.exists(_tmp_path):
                os.remove(_tmp_path)
        except OSError:
            pass
        raise

    return runs_visited, total_count


def _docx_reverse(src: str, dst: str, *, mapping) -> int:
    def _tf(txt: str) -> tuple[str, int]:
        return _reverse_text(txt, mapping)
    _, count = _walk_office(
        src, dst,
        text_tag=DOCX_TEXT_TAG,
        target_files_fn=_office_targets_docx,
        ns_uri=DOCX_NS,
        transform=_tf,
    )
    return count


def _pptx_reverse(src: str, dst: str, *, mapping) -> int:
    def _tf(txt: str) -> tuple[str, int]:
        return _reverse_text(txt, mapping)
    _, count = _walk_office(
        src, dst,
        text_tag=PPTX_TEXT_TAG,
        target_files_fn=_office_targets_pptx,
        ns_uri=PPTX_A_NS,
        transform=_tf,
    )
    return count


# ---------------------------------------------------------------------------
# XLSX — openpyxl, skip formulas
# ---------------------------------------------------------------------------


def _xlsx_reverse(src: str, dst: str, *, mapping) -> int:
    import openpyxl  # type: ignore
    wb = openpyxl.load_workbook(src)
    restored = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if not isinstance(v, str):
                    continue
                if v.startswith("="):
                    continue
                new, count = _reverse_text(v, mapping)
                if count:
                    cell.value = new
                    restored += count
    wb.save(dst)
    return restored


# ---------------------------------------------------------------------------
# Plain text + CSV
# ---------------------------------------------------------------------------


def _plain_reverse(src: str, dst: str, *, mapping) -> int:
    with open(src, "rb") as f:
        raw = f.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    new, count = _reverse_text(text, mapping)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(new)
    return count


def _csv_reverse(src: str, dst: str, *, mapping) -> int:
    rows_in: list[list[str]] = []
    with open(src, "r", encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            rows_in.append(row)
    restored = 0
    rows_out: list[list[str]] = []
    for row in rows_in:
        new_row = []
        for cell in row:
            new, count = _reverse_text(cell, mapping)
            restored += count
            new_row.append(new)
        rows_out.append(new_row)
    with open(dst, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows_out)
    return restored


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


_REVERSE_DISPATCH: dict[str, Callable[..., int]] = {
    ".docx": _docx_reverse,
    ".pptx": _pptx_reverse,
    ".xlsx": _xlsx_reverse,
    ".csv": _csv_reverse,
}


def deanonymize_file(src_path: str, dst_path: str, *, mapping) -> int:
    """Reverse pass. Returns count of tokens restored (sum across runs/cells).

    For unsupported types: copies src→dst unchanged and returns 0 (caller may
    have written a file we never pseudonymised — e.g. an LLM-generated PNG).
    """
    ext = os.path.splitext(src_path)[1].lower()
    if ext not in SUPPORTED_EXTS:
        if src_path != dst_path:
            shutil.copyfile(src_path, dst_path)
        return 0
    try:
        fn = _REVERSE_DISPATCH.get(ext)
        if fn is not None:
            return fn(src_path, dst_path, mapping=mapping)
        if ext in _PLAIN_EXTS or ext == ".json":
            return _plain_reverse(src_path, dst_path, mapping=mapping)
        raise FilePseudonymizeError(f"no walker registered for: {ext}")
    except FilePseudonymizeError:
        raise
    except Exception as e:
        raise FilePseudonymizeError(f"{ext} walker failed: {e}") from e


__all__ = [
    "SUPPORTED_EXTS",
    "FilePseudonymizeError",
    "deanonymize_file",
]
