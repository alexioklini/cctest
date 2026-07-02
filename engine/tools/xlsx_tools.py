"""Deterministic XLSX toolset — xlsx_inspect / xlsx_query / xlsx_create / xlsx_edit.

WHY (chats 2cb94154 / 98cceac2): spreadsheet quality used to depend entirely on
the chat model writing pandas/openpyxl code via python_exec — strong models
managed, weak ones churned through script iterations and delivered CSV instead
of styled workbooks. Like the docx/html pipeline (model writes markdown, code
renders the file), these tools make the model supply only INTENT (a SQL SELECT,
a small JSON spec) while the server moves the data deterministically:

  xlsx_inspect  — workbook profile (structure, column types, join-key candidates)
  xlsx_query    — ONE read-only SELECT over sheets loaded into in-memory SQLite
  xlsx_create   — declarative spec → styled workbook (house style preset)
  xlsx_edit     — declarative ops on an existing workbook, format-preserving

Bulk data never flows through the model: query results are capped for display
(full result → CSV artifact), create/edit pull rows server-side via
`source: {file, sheet?, sql?}`.

Wired per the 4-site rule (TOOL_DEFINITIONS / TOOL_GROUPS / impl here /
TOOL_DISPATCH). Reaches brain runtime via lazy `import brain as _brain`.
"""

from __future__ import annotations

import csv
import datetime
import json
import os
import re
import sqlite3

from engine.context import get_request_context
from engine.tool_exec import _ok, _err
from engine import doc_convert

_XLSX_EXTS = {".xlsx", ".xlsm"}
_CSV_EXTS = {".csv", ".tsv"}

# Caps: keep tool results small (the model only ever needs a preview — full
# results go to CSV artifacts) and refuse workbooks too big for an in-memory
# SQLite session.
QUERY_DISPLAY_ROWS = 50
QUERY_MAX_TOTAL_ROWS = 200_000
QUERY_MAX_FILE_MB = 30
CREATE_INLINE_MAX_CELLS = 5_000
INSPECT_SAMPLE_VALUES = 3
INSPECT_DISTINCT_SAMPLE = 5_000
JOIN_KEY_OVERLAP_SAMPLE = 200
JOIN_KEY_MIN_OVERLAP = 0.5

_NUMBER_FORMATS = {
    "text": "@",
    "int": "#,##0",
    "number": "#,##0.00",
    "eur": '#,##0.00 "€"',
    "percent": "0.0%",
    "date": "DD.MM.YYYY",
}

# Excel's default 3-color scale + traffic-light fills for CellIs rules.
_COND_FILLS = {"red": "F8696B", "green": "63BE7B", "yellow": "FFEB84"}

_UMLAUT_MAP = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
     "ß": "ss"})


# ---------------------------------------------------------------------------
# Grid loading (shared by inspect / query / create-source / edit-source)
# ---------------------------------------------------------------------------

def _cell_str(v) -> str:
    """Emptiness-preserving normalizer for the placeholder trim (matches the
    semantics of doc_convert._extract_xlsx's `_cell` — caps/pipe-escaping don't
    affect emptiness, so plain str+strip is equivalent for trimming)."""
    return "" if v is None else str(v).strip()


def _coerce_csv_value(s):
    """CSV cells arrive as strings; coerce int/float so SQL aggregates work.
    Comma-decimal locals stay text (ambiguous with thousands separators)."""
    if s is None:
        return None
    t = s.strip()
    if t == "":
        return None
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return s


def _detect_header_row(rows, scan: int = 10) -> int:
    """Index of the header row within the first `scan` rows: the first row
    whose non-empty cells are ≥60% strings (labels, not data). Falls back to
    the first non-empty row. Deterministic — no LLM."""
    fallback = None
    for i, r in enumerate(rows[:scan]):
        vals = [v for v in r if v is not None and str(v).strip() != ""]
        if not vals:
            continue
        if fallback is None:
            fallback = i
        str_ratio = sum(isinstance(v, str) for v in vals) / len(vals)
        if str_ratio >= 0.6:
            return i
    return fallback if fallback is not None else 0


def _resolve_input_path(raw: str) -> str:
    """Input files (attachments, artifacts) may be given relative — resolve
    against the session artifact folder first, then cwd. Reads are not
    restricted to the artifact folder (mirrors read_document)."""
    from engine.tools.file_tools import _resolve_artifact_dir
    p = os.path.expanduser(raw or "")
    if os.path.isabs(p):
        return p
    art_dir, _ = _resolve_artifact_dir()
    if art_dir:
        cand = os.path.join(art_dir, p)
        if os.path.exists(cand):
            return cand
    return os.path.abspath(p)


def _load_grids(path: str, sheet: str | None = None) -> list[dict]:
    """Load a workbook/CSV into plain grids:
    [{name, header:[str], rows:[[...]], header_row_idx}].
    Header row is detected, placeholder columns trimmed (the v9.261.0 logic,
    shared with doc_convert), empty header cells named col<N>."""
    ext = os.path.splitext(path)[1].lower()
    grids = []
    if ext in _CSV_EXTS:
        delim = "\t" if ext == ".tsv" else None
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
            sample = f.read(8192)
            f.seek(0)
            if delim is None:
                try:
                    delim = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
                except csv.Error:
                    delim = ";" if sample.count(";") > sample.count(",") else ","
            raw_rows = [[_coerce_csv_value(c) for c in row]
                        for row in csv.reader(f, delimiter=delim)]
        grids.append(_finish_grid(os.path.splitext(os.path.basename(path))[0],
                                  raw_rows))
        return grids

    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            if sheet and ws.title != sheet:
                continue
            raw_rows = [list(r) for r in ws.iter_rows(values_only=True)]
            g = _finish_grid(ws.title, raw_rows)
            if g is not None:
                grids.append(g)
    finally:
        try:
            wb.close()
        except Exception:
            pass
    if sheet and not grids:
        raise ValueError(f"sheet '{sheet}' not found in {os.path.basename(path)}")
    return grids


def _finish_grid(name: str, raw_rows: list) -> dict | None:
    if not any(any(_cell_str(c) for c in r) for r in raw_rows):
        return None
    hdr_idx = _detect_header_row(raw_rows)
    header = list(raw_rows[hdr_idx])
    data_rows = raw_rows[hdr_idx + 1:]
    header, data_rows = doc_convert._trim_placeholder_columns(
        header, data_rows, _cell_str)
    names = []
    for i, h in enumerate(header):
        hs = _cell_str(h)
        names.append(hs if hs else f"col{i + 1}")
    n = len(names)
    norm_rows = []
    for r in data_rows:
        row = list(r[:n]) + [None] * (n - len(r))
        if all(_cell_str(c) == "" for c in row):
            continue  # skip blank rows
        norm_rows.append(row)
    return {"name": name, "header": names, "rows": norm_rows,
            "header_row_idx": hdr_idx}


# ---------------------------------------------------------------------------
# Name sanitization + SQLite session
# ---------------------------------------------------------------------------

def _sanitize_name(s: str, used: set) -> str:
    t = str(s).strip().translate(_UMLAUT_MAP)
    t = re.sub(r"[^0-9a-zA-Z_]+", "_", t).strip("_").lower() or "t"
    if t[0].isdigit():
        t = "t_" + t
    base, n = t, 2
    while t in used:
        t = f"{base}_{n}"
        n += 1
    used.add(t)
    return t


def _to_sql_value(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, datetime.datetime):
        # Midnight timestamps are Excel "dates" — keep them date-shaped so
        # WHERE datum = '2026-01-02' works the way the model expects.
        if v.hour == v.minute == v.second == 0 and v.microsecond == 0:
            return v.date().isoformat()
        return v.isoformat(sep=" ")
    if isinstance(v, (datetime.date, datetime.time)):
        return v.isoformat()
    return v


def _build_sqlite(paths: list[str], sheet: str | None = None):
    """Load every sheet of every file into ONE in-memory SQLite DB.
    Returns (conn, tables) — tables = [{table, file, sheet, columns:
    [(sql_name, orig_name)], rows}]. With >1 file, table names are prefixed
    with the file stem so same-named sheets don't collide (and cross-file
    joins read naturally: orders_alt vs orders_neu)."""
    total_rows = 0
    loaded = []  # (file, grid)
    for p in paths:
        rp = _resolve_input_path(p)
        if not os.path.isfile(rp):
            raise FileNotFoundError(f"File not found: {p}")
        size_mb = os.path.getsize(rp) / (1024 * 1024)
        if size_mb > QUERY_MAX_FILE_MB:
            raise ValueError(
                f"{os.path.basename(rp)} is {size_mb:.0f} MB (> {QUERY_MAX_FILE_MB} MB). "
                f"Pass sheet='<name>' to load a single sheet, or split the file.")
        for g in _load_grids(rp, sheet=sheet):
            total_rows += len(g["rows"])
            if total_rows > QUERY_MAX_TOTAL_ROWS:
                raise ValueError(
                    f"More than {QUERY_MAX_TOTAL_ROWS:,} rows across the loaded "
                    f"sheets. Pass sheet='<name>' to load a single sheet.")
            loaded.append((rp, g))

    conn = sqlite3.connect(":memory:")
    used_tables: set = set()
    multi = len(paths) > 1
    tables = []
    for rp, g in loaded:
        stem = os.path.splitext(os.path.basename(rp))[0]
        raw_name = f"{stem}_{g['name']}" if multi else g["name"]
        tname = _sanitize_name(raw_name, used_tables)
        used_cols: set = set()
        cols = [(_sanitize_name(h, used_cols), h) for h in g["header"]]
        col_sql = ", ".join(f'"{c}"' for c, _ in cols)
        conn.execute(f'CREATE TABLE "{tname}" ({col_sql})')
        ph = ", ".join(["?"] * len(cols))
        conn.executemany(
            f'INSERT INTO "{tname}" VALUES ({ph})',
            ([_to_sql_value(v) for v in row] for row in g["rows"]))
        tables.append({"table": tname, "file": os.path.basename(rp),
                       "sheet": g["name"], "columns": cols,
                       "rows": len(g["rows"])})
    conn.commit()
    # Read-only from here: PRAGMA first (the authorizer would deny it), then
    # deny everything except reading — belt and braces on top of the
    # SELECT/WITH prefix check in tool_xlsx_query.
    conn.execute("PRAGMA query_only=ON")
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
               sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def _authorizer(action, *_):
        return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY

    conn.set_authorizer(_authorizer)
    return conn, tables


def _schema_echo(tables: list[dict]) -> str:
    lines = ["Tables for xlsx_query:"]
    for t in tables:
        cols = ", ".join(c for c, _ in t["columns"])
        lines.append(f"- {t['table']}({cols}) — {t['rows']} rows "
                     f"[{t['file']} / {t['sheet']}]")
    return "\n".join(lines)


def _check_select_only(sql: str) -> str | None:
    """SELECT/WITH single statements only. Returns an error message or None."""
    body = (sql or "").strip().rstrip(";").strip()
    if not body:
        return "empty sql"
    if not re.match(r"(?is)^(select|with)\b", body):
        return "only a single SELECT statement is allowed (start with SELECT or WITH)"
    if ";" in body:
        return ("only ONE statement is allowed — remove the ';' and send a "
                "single SELECT")
    return None


def _markdown_table(headers: list[str], rows: list) -> str:
    def esc(v):
        return "" if v is None else str(v).replace("|", "\\|").replace("\n", " ")
    out = ["| " + " | ".join(esc(h) for h in headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(esc(c) for c in r) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# xlsx_inspect
# ---------------------------------------------------------------------------

def _infer_col_type(values) -> str:
    kinds = set()
    for v in values:
        if isinstance(v, bool):
            kinds.add("bool")
        elif isinstance(v, int):
            kinds.add("int")
        elif isinstance(v, float):
            kinds.add("float")
        elif isinstance(v, (datetime.datetime, datetime.date)):
            kinds.add("date")
        else:
            kinds.add("text")
    if not kinds:
        return "empty"
    if kinds <= {"int", "bool"}:
        return "int"
    if kinds <= {"int", "float", "bool"}:
        return "number"
    if kinds == {"date"}:
        return "date"
    return "text" if len(kinds) > 1 or "text" in kinds else kinds.pop()


def _fmt_sample(v) -> str:
    s = _cell_str(_to_sql_value(v))
    return s[:40] + "…" if len(s) > 40 else s


def _workbook_extras(path: str) -> dict:
    """Merged ranges, named ranges and formula count need extra passes that
    the values-grid can't provide (data_only=True hides formulas; read_only
    hides merges). Best-effort — inspection must not fail on exotic files."""
    ext = os.path.splitext(path)[1].lower()
    out = {"merged": {}, "named_ranges": [], "formulas": {}}
    if ext not in _XLSX_EXTS:
        return out
    import openpyxl
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=False)
        for ws in wb.worksheets:
            n = 0
            for row in ws.iter_rows(values_only=True):
                n += sum(1 for c in row
                         if isinstance(c, str) and c.startswith("="))
            out["formulas"][ws.title] = n
        try:
            out["named_ranges"] = list(wb.defined_names.keys())
        except Exception:
            pass
        wb.close()
    except Exception:
        pass
    try:
        wb2 = openpyxl.load_workbook(path, data_only=True)
        for ws in wb2.worksheets:
            out["merged"][ws.title] = len(ws.merged_cells.ranges)
        wb2.close()
    except Exception:
        pass
    return out


def _join_key_candidates(sheet_infos: list[dict]) -> list[str]:
    """Same-named columns across sheets whose value sets overlap — the model
    gets the join key handed to it instead of guessing (the marktorder case:
    MARKTORDERNUMMER appears in both sheets with ~full overlap)."""
    hits = []
    for i in range(len(sheet_infos)):
        for j in range(i + 1, len(sheet_infos)):
            a, b = sheet_infos[i], sheet_infos[j]
            cols_b = {c["sql"]: c for c in b["cols"]}
            for ca in a["cols"]:
                cb = cols_b.get(ca["sql"])
                if cb is None:
                    continue
                va, vb = ca["value_sample"], cb["value_sample"]
                if not va or not vb:
                    continue
                overlap = len(va & vb) / min(len(va), len(vb))
                if overlap >= JOIN_KEY_MIN_OVERLAP:
                    hits.append(
                        f"- `{ca['name']}`: {a['table']} ↔ {b['table']} "
                        f"(value overlap {overlap:.0%}) — likely JOIN key")
    return hits


def tool_xlsx_inspect(args: dict) -> str:
    import brain as _brain
    paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
    if not paths:
        return _err("xlsx_inspect: 'path' (or 'paths') is required")
    sheet = args.get("sheet")
    try:
        parts = []
        sheet_infos = []
        used_tables: set = set()
        multi = len(paths) > 1
        for p in paths:
            rp = _resolve_input_path(p)
            if not os.path.isfile(rp):
                return _err(f"File not found: {p}")
            extras = _workbook_extras(rp)
            grids = _load_grids(rp, sheet=sheet)
            if not grids:
                parts.append(f"# {os.path.basename(rp)}\n\n(no data found)")
                continue
            parts.append(f"# {os.path.basename(rp)}")
            for g in grids:
                stem = os.path.splitext(os.path.basename(rp))[0]
                raw_name = f"{stem}_{g['name']}" if multi else g["name"]
                tname = _sanitize_name(raw_name, used_tables)
                used_cols: set = set()
                n_rows = len(g["rows"])
                parts.append(
                    f"\n## Sheet: {g['name']} — {n_rows} data rows × "
                    f"{len(g['header'])} columns (header row "
                    f"{g['header_row_idx'] + 1})")
                extra_bits = []
                if extras["merged"].get(g["name"]):
                    extra_bits.append(f"{extras['merged'][g['name']]} merged ranges")
                if extras["formulas"].get(g["name"]):
                    extra_bits.append(f"{extras['formulas'][g['name']]} formula cells")
                if extra_bits:
                    parts.append("_" + ", ".join(extra_bits) + "_")
                from openpyxl.utils import get_column_letter
                col_rows = []
                cols_info = []
                for ci, h in enumerate(g["header"]):
                    values = [r[ci] for r in g["rows"] if ci < len(r)]
                    non_null = [v for v in values if _cell_str(v) != ""]
                    ctype = _infer_col_type(non_null[:INSPECT_DISTINCT_SAMPLE])
                    distinct_sample = set()
                    for v in non_null[:INSPECT_DISTINCT_SAMPLE]:
                        distinct_sample.add(_to_sql_value(v))
                    n_distinct = len(distinct_sample)
                    distinct_str = (f"{n_distinct}"
                                    if len(non_null) <= INSPECT_DISTINCT_SAMPLE
                                    else f"{n_distinct}+")
                    minmax = ""
                    if ctype in ("int", "number", "date") and non_null:
                        try:
                            lo = min(_to_sql_value(v) for v in non_null)
                            hi = max(_to_sql_value(v) for v in non_null)
                            minmax = f"{_fmt_sample(lo)} – {_fmt_sample(hi)}"
                        except TypeError:
                            pass
                    samples = []
                    for v in non_null:
                        s = _fmt_sample(v)
                        if s not in samples:
                            samples.append(s)
                        if len(samples) >= INSPECT_SAMPLE_VALUES:
                            break
                    sql_name = _sanitize_name(h, used_cols)
                    col_rows.append([
                        get_column_letter(ci + 1), h, sql_name, ctype,
                        len(values) - len(non_null), distinct_str, minmax,
                        ", ".join(samples)])
                    cols_info.append({
                        "name": h, "sql": sql_name,
                        "value_sample": set(
                            _to_sql_value(v) for v in
                            non_null[:JOIN_KEY_OVERLAP_SAMPLE])})
                parts.append(_markdown_table(
                    ["Col", "Name", "SQL name", "Type", "Nulls", "Distinct",
                     "Min–Max", "Samples"], col_rows))
                sheet_infos.append({"table": tname, "cols": cols_info,
                                    "file": os.path.basename(rp),
                                    "sheet": g["name"], "rows": n_rows,
                                    "columns": [(c["sql"], c["name"])
                                                for c in cols_info]})
            if extras["named_ranges"]:
                parts.append("\n_Named ranges: "
                             + ", ".join(extras["named_ranges"][:20]) + "_")
        joins = _join_key_candidates(sheet_infos)
        if joins:
            parts.append("\n## Join-key candidates\n" + "\n".join(joins))
        parts.append("\n## " + _schema_echo(sheet_infos))
        report = "\n".join(parts)
        src = "xlsx:" + ",".join(os.path.basename(_resolve_input_path(p))
                                 for p in paths)
        report = _brain._gdpr_anon_tool_text(report, src)
        return _ok({"paths": paths, "report": report})
    except Exception as e:
        return _err(f"xlsx_inspect: {type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# xlsx_query
# ---------------------------------------------------------------------------

def tool_xlsx_query(args: dict) -> str:
    import brain as _brain
    paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
    if not paths:
        return _err("xlsx_query: 'path' (or 'paths') is required")
    sql = args.get("sql") or ""
    sel_err = _check_select_only(sql)
    if sel_err:
        return _err(f"xlsx_query: {sel_err}")
    sql = sql.strip().rstrip(";")
    try:
        conn, tables = _build_sqlite(paths, sheet=args.get("sheet"))
    except (FileNotFoundError, ValueError) as e:
        return _err(f"xlsx_query: {e}")
    except Exception as e:
        return _err(f"xlsx_query: {type(e).__name__}: {e}")
    try:
        try:
            cur = conn.execute(sql)
        except sqlite3.Error as e:
            # The self-correction loop: echo the real schema so the model can
            # fix identifiers in one round instead of guessing again.
            return _err(f"xlsx_query: SQL error: {e}\n\n{_schema_echo(tables)}")
        headers = [d[0] for d in cur.description or []]
        rows = cur.fetchall()
        row_count = len(rows)
        display = rows[:QUERY_DISPLAY_ROWS]
        md = _markdown_table(headers, display)
        if row_count > QUERY_DISPLAY_ROWS:
            md += (f"\n\n_({row_count:,} rows total, showing first "
                   f"{QUERY_DISPLAY_ROWS} — pass out='name.csv' to save the "
                   f"full result)_")
        out_info = None
        out_name = (args.get("out") or "").strip()
        if out_name:
            from engine.tools.file_tools import _enforce_artifact_path
            if not out_name.lower().endswith(".csv"):
                out_name += ".csv"
            out_path, perr = _enforce_artifact_path(out_name, "xlsx_query")
            if perr:
                return perr
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(headers)
                w.writerows(rows)
            agent = get_request_context().current_agent
            _brain._after_file_write(
                out_path, "created", agent.agent_id if agent else "main")
            out_info = {"path": out_path, "rows": row_count}
        src = "xlsx_query:" + ",".join(os.path.basename(p) for p in paths)
        md = _brain._gdpr_anon_tool_text(md, src)
        res = {"row_count": row_count, "result": md}
        if out_info:
            res["saved"] = out_info
        return _ok(res)
    except Exception as e:
        return _err(f"xlsx_query: {type(e).__name__}: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# xlsx_create — declarative spec → styled workbook
# ---------------------------------------------------------------------------

def _hex(color: str, fallback: str) -> str:
    c = (color or fallback or "").lstrip("#").upper()
    return c if re.fullmatch(r"[0-9A-F]{6}", c) else fallback.lstrip("#").upper()


def _style_kit(style: dict) -> dict:
    """Derive the small set of openpyxl style objects from a doc-style preset
    (the same YAML presets docx/html use — one house look everywhere)."""
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    colors = style.get("colors") or {}
    docx = style.get("docx") or {}
    header_bg = _hex(colors.get("table_header_bg"), "#1F3864")
    header_fg = _hex(colors.get("table_header_text"), "#FFFFFF")
    zebra = _hex(docx.get("zebra_fill"), "#EDF1F8")
    thin = Side(style="thin", color="B4B4B4")
    return {
        "header_font": Font(bold=True, color=header_fg,
                            name=(style.get("fonts") or {}).get("body", "Calibri")),
        "header_fill": PatternFill("solid", start_color=header_bg,
                                   end_color=header_bg),
        "header_align": Alignment(horizontal="center", vertical="center",
                                  wrap_text=True),
        "zebra_fill": PatternFill("solid", start_color=zebra, end_color=zebra),
        "master_fill": PatternFill("solid", start_color=zebra, end_color=zebra),
        "detail_fill": PatternFill("solid", start_color="F7F7F7",
                                   end_color="F7F7F7"),
        "border": Border(left=thin, right=thin, top=thin, bottom=thin),
    }


def _clean_sheet_name(name: str, used: set) -> str:
    t = re.sub(r"[\[\]:*?/\\]", "", str(name or "Sheet")).strip() or "Sheet"
    t = t[:31]
    base, n = t, 2
    while t in used:
        suffix = f"_{n}"
        t = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(t)
    return t


def _resolve_source(source: dict) -> tuple[list[str], list[list]]:
    """A create/edit data source: {file, sheet?, sql?} → (header, rows).
    Data flows file→file server-side; the model never transcribes rows."""
    if not isinstance(source, dict) or not source.get("file"):
        raise ValueError("source needs {file, sheet?, sql?}")
    fpath = source["file"]
    sql = (source.get("sql") or "").strip()
    if sql:
        sel_err = _check_select_only(sql)
        if sel_err:
            raise ValueError(f"source.sql: {sel_err}")
        conn, tables = _build_sqlite([fpath], sheet=source.get("sheet"))
        try:
            try:
                cur = conn.execute(sql.rstrip(";"))
            except sqlite3.Error as e:
                raise ValueError(f"source.sql error: {e}\n{_schema_echo(tables)}")
            headers = [d[0] for d in cur.description or []]
            return headers, [list(r) for r in cur.fetchall()]
        finally:
            conn.close()
    rp = _resolve_input_path(fpath)
    if not os.path.isfile(rp):
        raise FileNotFoundError(f"File not found: {fpath}")
    grids = _load_grids(rp, sheet=source.get("sheet"))
    if not grids:
        raise ValueError(f"no data found in {os.path.basename(rp)}")
    g = grids[0]
    return g["header"], g["rows"]


def _sheet_data(sheet_spec: dict) -> tuple[list[str], list[list]]:
    """Resolve the (exactly one) data source of a create-spec sheet."""
    cols = sheet_spec.get("columns") or []
    col_names = [c.get("name") for c in cols if isinstance(c, dict)]
    if sheet_spec.get("rows") is not None:
        rows = sheet_spec["rows"]
        n_cells = sum(len(r) for r in rows if isinstance(r, (list, tuple)))
        if n_cells > CREATE_INLINE_MAX_CELLS:
            raise ValueError(
                f"inline rows too large ({n_cells} cells > "
                f"{CREATE_INLINE_MAX_CELLS}). Do NOT copy data into the spec — "
                f"use source: {{file, sheet?, sql?}} so the server moves the "
                f"data itself.")
        if col_names and all(col_names):
            return list(col_names), [list(r) for r in rows]
        if not rows:
            # An intentionally empty sheet (write_document markdown sections
            # without a table produce this) — render as a bare sheet.
            return [], []
        return [str(c) for c in rows[0]], [list(r) for r in rows[1:]]
    if sheet_spec.get("source"):
        header, rows = _resolve_source(sheet_spec["source"])
        if col_names and all(col_names):
            idx = _column_indices(header, col_names)
            rows = [[r[i] for i in idx] for r in rows]
            header = list(col_names)
        return header, rows
    raise ValueError("sheet needs exactly one of: rows, source, master_detail")


def _column_indices(header: list[str], wanted: list[str]) -> list[int]:
    lower = {str(h).strip().lower(): i for i, h in enumerate(header)}
    idx = []
    for w in wanted:
        i = lower.get(str(w).strip().lower())
        if i is None:
            raise ValueError(
                f"column '{w}' not found — available: {', '.join(map(str, header))}")
        idx.append(i)
    return idx


def _apply_header_row(ws, header, kit, row=1):
    for ci, h in enumerate(header, 1):
        c = ws.cell(row=row, column=ci, value=str(h))
        c.font = kit["header_font"]
        c.fill = kit["header_fill"]
        c.alignment = kit["header_align"]
        c.border = kit["border"]


def _auto_widths(ws, header, rows, col_specs=None):
    from openpyxl.utils import get_column_letter
    widths = {}
    for ci, h in enumerate(header):
        w = len(str(h))
        for r in rows[:200]:
            if ci < len(r) and r[ci] is not None:
                w = max(w, len(str(r[ci])))
        widths[ci] = min(60, w + 2)
    if col_specs:
        for ci, cs in enumerate(col_specs):
            if isinstance(cs, dict) and cs.get("width"):
                widths[ci] = cs["width"]
    for ci, w in widths.items():
        ws.column_dimensions[get_column_letter(ci + 1)].width = max(8, w)


def _apply_number_formats(ws, header, col_specs, first_row, last_row):
    if not col_specs:
        return
    by_name = {str(c.get("name", "")).strip().lower(): c
               for c in col_specs if isinstance(c, dict)}
    for ci, h in enumerate(header, 1):
        cs = by_name.get(str(h).strip().lower())
        fmt = _NUMBER_FORMATS.get((cs or {}).get("format") or "")
        if not fmt:
            continue
        for r in range(first_row, last_row + 1):
            ws.cell(row=r, column=ci).number_format = fmt


def _render_charts(ws, sheet_spec, header, n_data_rows, header_row=1):
    charts = sheet_spec.get("charts") or []
    if not charts or n_data_rows == 0:
        return
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.utils import get_column_letter
    kinds = {"bar": BarChart, "line": LineChart, "pie": PieChart}
    anchor_col = len(header) + 2
    anchor_row = 2
    for ch in charts:
        if not isinstance(ch, dict):
            continue
        cls = kinds.get((ch.get("type") or "bar").lower())
        if cls is None:
            raise ValueError(f"chart type '{ch.get('type')}' — use bar|line|pie")
        series_names = ch.get("series") or []
        label_name = ch.get("labels")
        if not series_names:
            raise ValueError("chart needs series: ['<column>', …]")
        s_idx = _column_indices(header, series_names)
        chart = cls()
        chart.title = ch.get("title") or None
        first, last = header_row + 1, header_row + n_data_rows
        for si in s_idx:
            ref = Reference(ws, min_col=si + 1, min_row=header_row,
                            max_row=last)
            chart.add_data(ref, titles_from_data=True)
        if label_name:
            li = _column_indices(header, [label_name])[0]
            chart.set_categories(
                Reference(ws, min_col=li + 1, min_row=first, max_row=last))
        pos = ch.get("position") or f"{get_column_letter(anchor_col)}{anchor_row}"
        ws.add_chart(chart, pos)
        anchor_row += 16  # stack subsequent charts below each other


def _render_conditional(ws, sheet_spec, header, n_data_rows, header_row=1):
    rules = sheet_spec.get("conditional") or []
    if not rules or n_data_rows == 0:
        return
    from openpyxl.formatting.rule import (ColorScaleRule, DataBarRule,
                                          CellIsRule)
    from openpyxl.styles import PatternFill
    from openpyxl.utils import get_column_letter
    first, last = header_row + 1, header_row + n_data_rows
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        cols = rule.get("columns") or []
        spec = rule.get("rule")
        for name in cols:
            ci = _column_indices(header, [name])[0]
            letter = get_column_letter(ci + 1)
            rng = f"{letter}{first}:{letter}{last}"
            if spec == "color_scale":
                ws.conditional_formatting.add(rng, ColorScaleRule(
                    start_type="min", start_color="F8696B",
                    mid_type="percentile", mid_value=50, mid_color="FFEB84",
                    end_type="max", end_color="63BE7B"))
            elif spec == "data_bars":
                ws.conditional_formatting.add(rng, DataBarRule(
                    start_type="min", end_type="max", color="638EC6",
                    showValue=True))
            elif isinstance(spec, dict):
                op_map = {"lt": "lessThan", "gt": "greaterThan", "eq": "equal"}
                op = next((k for k in op_map if k in spec), None)
                if op is None:
                    raise ValueError(
                        "conditional rule dict needs lt|gt|eq, e.g. "
                        '{"lt": 0, "fill": "red"}')
                fill_hex = _COND_FILLS.get(
                    (spec.get("fill") or "red").lower(), _COND_FILLS["red"])
                val = spec[op]
                formula = [f'"{val}"' if isinstance(val, str) else str(val)]
                ws.conditional_formatting.add(rng, CellIsRule(
                    operator=op_map[op], formula=formula,
                    fill=PatternFill("solid", start_color=fill_hex,
                                     end_color=fill_hex)))
            else:
                raise ValueError(
                    f"conditional rule '{spec}' — use \"color_scale\", "
                    f"\"data_bars\" or {{\"lt\"|\"gt\"|\"eq\": value, "
                    f"\"fill\": \"red|green|yellow\"}}")


def _render_table_sheet(ws, sheet_spec, kit) -> int:
    header, rows = _sheet_data(sheet_spec)
    if not header:
        return 0  # intentionally empty sheet
    col_specs = sheet_spec.get("columns") or []
    _apply_header_row(ws, header, kit)
    banded = sheet_spec.get("banded", True)
    for ri, row in enumerate(rows):
        for ci in range(len(header)):
            v = row[ci] if ci < len(row) else None
            c = ws.cell(row=ri + 2, column=ci + 1, value=v)
            c.border = kit["border"]
            if banded and ri % 2 == 1:
                c.fill = kit["zebra_fill"]
    n = len(rows)
    _apply_number_formats(ws, header, col_specs, 2, n + 1)
    totals = sheet_spec.get("totals") or []
    if totals and n:
        from openpyxl.utils import get_column_letter
        from openpyxl.styles import Font
        t_idx = _column_indices(header, totals)
        trow = n + 2
        first_cell = ws.cell(row=trow, column=1)
        if 0 not in t_idx:
            first_cell.value = "Summe"
        for ci in t_idx:
            letter = get_column_letter(ci + 1)
            ws.cell(row=trow, column=ci + 1,
                    value=f"=SUM({letter}2:{letter}{n + 1})")
        for ci in range(len(header)):
            c = ws.cell(row=trow, column=ci + 1)
            c.font = Font(bold=True)
            c.border = kit["border"]
        _apply_number_formats(ws, header, col_specs, trow, trow)
    if sheet_spec.get("freeze_header", True):
        ws.freeze_panes = "A2"
    _auto_widths(ws, header, rows, col_specs)
    _render_charts(ws, sheet_spec, header, n)
    _render_conditional(ws, sheet_spec, header, n)
    return n + (1 if totals and n else 0)


def _render_master_detail_sheet(ws, sheet_spec, kit) -> int:
    """The marktorder shape: one master row (tinted, bold key), its detail
    rows grouped beneath (outline level 1, collapsible), detail columns offset
    to the right of the master columns, one frozen combined header."""
    md = sheet_spec["master_detail"]
    key = md.get("key")
    if not key:
        raise ValueError("master_detail needs key: '<column name>'")
    m_header, m_rows = _resolve_source((md.get("master") or {}).get("source")
                                       or md.get("master"))
    d_header, d_rows = _resolve_source((md.get("detail") or {}).get("source")
                                       or md.get("detail"))
    m_cols = (md.get("master") or {}).get("columns") or m_header
    d_cols = ((md.get("detail") or {}).get("columns")
              or [h for h in d_header
                  if str(h).strip().lower() != str(key).strip().lower()])
    m_idx = _column_indices(m_header, m_cols)
    d_idx = _column_indices(d_header, d_cols)
    m_key = _column_indices(m_header, [key])[0]
    d_key = _column_indices(d_header, [key])[0]

    detail_by_key: dict = {}
    for r in d_rows:
        detail_by_key.setdefault(_cell_str(_to_sql_value(r[d_key])), []).append(r)

    combined = [m_header[i] for i in m_idx] + [d_header[i] for i in d_idx]
    _apply_header_row(ws, combined, kit)
    from openpyxl.styles import Font
    bold = Font(bold=True)
    n_m = len(m_idx)
    row_no = 2
    key_out_idx = m_idx.index(m_key) if m_key in m_idx else None
    for mr in m_rows:
        for ci, si in enumerate(m_idx):
            c = ws.cell(row=row_no, column=ci + 1,
                        value=mr[si] if si < len(mr) else None)
            c.fill = kit["master_fill"]
            c.border = kit["border"]
            if key_out_idx is not None and ci == key_out_idx:
                c.font = bold
        for ci in range(n_m, n_m + len(d_idx)):
            c = ws.cell(row=row_no, column=ci + 1)
            c.fill = kit["master_fill"]
            c.border = kit["border"]
        row_no += 1
        for dr in detail_by_key.get(_cell_str(_to_sql_value(mr[m_key])), []):
            for ci, si in enumerate(d_idx):
                c = ws.cell(row=row_no, column=n_m + ci + 1,
                            value=dr[si] if si < len(dr) else None)
                c.fill = kit["detail_fill"]
                c.border = kit["border"]
            ws.row_dimensions[row_no].outline_level = 1
            row_no += 1
    if sheet_spec.get("freeze_header", True):
        ws.freeze_panes = "A2"
    sample_rows = [[None] * len(combined)]
    _auto_widths(ws, combined, sample_rows)
    n_data = row_no - 2
    _apply_number_formats(ws, combined, sheet_spec.get("columns") or [],
                          2, row_no - 1)
    _render_charts(ws, sheet_spec, combined, n_data)
    _render_conditional(ws, sheet_spec, combined, n_data)
    return n_data


def render_spec(path: str, spec: dict, style_name: str = "",
                style: dict | None = None) -> dict:
    """Render a declarative workbook spec to `path`. Shared by xlsx_create and
    the write_document .xlsx branch (which builds a spec from markdown tables
    and passes its already-loaded `style` dict). Raises ValueError/
    FileNotFoundError with model-actionable messages."""
    from engine.tools.file_tools import _resolve_default_style, _load_doc_style
    import openpyxl
    if not isinstance(spec, dict) or not isinstance(spec.get("sheets"), list) \
            or not spec["sheets"]:
        raise ValueError('spec needs {"sheets": [{...}]}')
    if style is None:
        style = _load_doc_style(_resolve_default_style(
            style_name or spec.get("style") or ""))
    kit = _style_kit(style)
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    used_names: set = set()
    out_sheets = []
    for sheet_spec in spec["sheets"]:
        if not isinstance(sheet_spec, dict):
            raise ValueError("each sheet must be an object")
        name = _clean_sheet_name(sheet_spec.get("name"), used_names)
        ws = wb.create_sheet(name)
        if sheet_spec.get("master_detail"):
            n = _render_master_detail_sheet(ws, sheet_spec, kit)
        else:
            n = _render_table_sheet(ws, sheet_spec, kit)
        out_sheets.append({"name": name, "rows": n})
    wb.save(path)
    return {"sheets": out_sheets}


def tool_xlsx_create(args: dict) -> str:
    import brain as _brain
    from engine.tools.file_tools import _enforce_artifact_path
    raw_path = (args.get("path") or "").strip()
    if not raw_path:
        return _err("xlsx_create: 'path' is required (e.g. 'report.xlsx')")
    if not raw_path.lower().endswith(".xlsx"):
        raw_path += ".xlsx"
    spec = args.get("spec")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError as e:
            return _err(f"xlsx_create: spec is not valid JSON: {e}")
    path, perr = _enforce_artifact_path(raw_path, "xlsx_create")
    if perr:
        return perr
    try:
        info = render_spec(path, spec or {})
    except (ValueError, FileNotFoundError) as e:
        return _err(f"xlsx_create: {e}")
    except Exception as e:
        return _err(f"xlsx_create: {type(e).__name__}: {e}")
    agent = get_request_context().current_agent
    _brain._after_file_write(path, "created",
                             agent.agent_id if agent else "main")
    size = os.path.getsize(path)
    res = {"path": path, "size": size, "sheets": info["sheets"],
           "status": "written"}
    if size > 5 * 1024 * 1024:
        res["note"] = "file exceeds the 5MB artifact-version snapshot cap"
    return _ok(res)


# ---------------------------------------------------------------------------
# xlsx_edit — declarative ops on an existing workbook, format-preserving
# ---------------------------------------------------------------------------

def _copy_row_styles(ws, src_row: int, dst_row: int, n_cols: int):
    from copy import copy
    for ci in range(1, n_cols + 1):
        s, d = ws.cell(row=src_row, column=ci), ws.cell(row=dst_row, column=ci)
        if s.has_style:
            d._style = copy(s._style)


def _edit_header_map(ws) -> dict:
    """Column name → index from row 1 (xlsx_edit assumes header in row 1 —
    stated in the tool description)."""
    return {str(c.value).strip().lower(): c.column
            for c in ws[1] if c.value is not None}


def _op_rows(op: dict) -> list[list]:
    if op.get("rows") is not None:
        return [list(r) for r in op["rows"]]
    if op.get("source"):
        _, rows = _resolve_source(op["source"])
        return rows
    raise ValueError(f"{op.get('op')} needs rows or source")


def _apply_edit_ops(wb, ops: list, kit) -> list[dict]:
    applied = []
    used_names = {ws.title for ws in wb.worksheets}
    for op in ops:
        if not isinstance(op, dict) or not op.get("op"):
            raise ValueError('each op needs {"op": "..."}')
        kind = op["op"]
        if kind == "add_sheet":
            name = _clean_sheet_name(op.get("name"), used_names)
            ws = wb.create_sheet(name)
            if op.get("master_detail"):
                n = _render_master_detail_sheet(ws, op, kit)
            else:
                n = _render_table_sheet(ws, op, kit)
            applied.append({"op": kind, "sheet": name, "rows_affected": n})
            continue
        if kind == "rename_sheet":
            src = op.get("from") or op.get("sheet")
            if src not in wb.sheetnames:
                raise ValueError(
                    f"sheet '{src}' not found — sheets: {wb.sheetnames}")
            ws = wb[src]
            ws.title = str(op.get("to") or "")[:31]
            used_names.discard(src)
            used_names.add(ws.title)
            applied.append({"op": kind, "sheet": ws.title, "rows_affected": 0})
            continue
        if kind == "delete_sheet":
            name = op.get("name") or op.get("sheet")
            if name not in wb.sheetnames:
                raise ValueError(
                    f"sheet '{name}' not found — sheets: {wb.sheetnames}")
            if len(wb.sheetnames) == 1:
                raise ValueError("cannot delete the only sheet")
            wb.remove(wb[name])
            applied.append({"op": kind, "sheet": name, "rows_affected": 0})
            continue

        sheet_name = op.get("sheet")
        if sheet_name not in wb.sheetnames:
            raise ValueError(
                f"sheet '{sheet_name}' not found — sheets: {wb.sheetnames}")
        ws = wb[sheet_name]
        hdr = _edit_header_map(ws)

        if kind == "append_rows":
            rows = _op_rows(op)
            template_row = ws.max_row if ws.max_row >= 2 else None
            n_cols = ws.max_column
            start = ws.max_row + 1
            for ri, row in enumerate(rows):
                for ci, v in enumerate(row[:n_cols], 1):
                    ws.cell(row=start + ri, column=ci, value=v)
                if template_row:
                    _copy_row_styles(ws, template_row, start + ri, n_cols)
            applied.append({"op": kind, "sheet": sheet_name,
                            "rows_affected": len(rows)})
        elif kind == "add_column":
            name = op.get("name")
            if not name:
                raise ValueError("add_column needs name")
            ci = ws.max_column + 1
            from copy import copy
            hcell = ws.cell(row=1, column=ci, value=name)
            neighbor = ws.cell(row=1, column=ci - 1)
            if neighbor.has_style:
                hcell._style = copy(neighbor._style)
            values = op.get("values")
            formula = op.get("formula")
            n = 0
            for r in range(2, ws.max_row + 1):
                cell = ws.cell(row=r, column=ci)
                if formula:
                    cell.value = formula.replace("{row}", str(r))
                elif values is not None:
                    if r - 2 < len(values):
                        cell.value = values[r - 2]
                body_neighbor = ws.cell(row=r, column=ci - 1)
                if body_neighbor.has_style:
                    cell._style = copy(body_neighbor._style)
                fmt = _NUMBER_FORMATS.get(op.get("format") or "")
                if fmt:
                    cell.number_format = fmt
                n += 1
            applied.append({"op": kind, "sheet": sheet_name,
                            "rows_affected": n})
        elif kind == "update_cells":
            where = op.get("where") or {}
            setter = op.get("set") or {}
            wcol = hdr.get(str(where.get("column", "")).strip().lower())
            if wcol is None:
                raise ValueError(
                    f"where.column '{where.get('column')}' not found — "
                    f"columns: {sorted(hdr)}")
            target_cols = {}
            for k, v in setter.items():
                tc = hdr.get(str(k).strip().lower())
                if tc is None:
                    raise ValueError(
                        f"set column '{k}' not found — columns: {sorted(hdr)}")
                target_cols[tc] = v
            if not target_cols:
                raise ValueError("update_cells needs set: {column: value}")
            cond_op = next((k for k in ("equals", "contains", "lt", "gt")
                            if k in where), None)
            if cond_op is None:
                raise ValueError(
                    "where needs one of equals|contains|lt|gt")
            ref = where[cond_op]
            n = 0
            for r in range(2, ws.max_row + 1):
                v = ws.cell(row=r, column=wcol).value
                hit = False
                try:
                    if cond_op == "equals":
                        hit = (v == ref or _cell_str(v) == _cell_str(ref))
                    elif cond_op == "contains":
                        hit = str(ref).lower() in _cell_str(v).lower()
                    elif cond_op == "lt":
                        hit = v is not None and v < ref
                    elif cond_op == "gt":
                        hit = v is not None and v > ref
                except TypeError:
                    hit = False
                if hit:
                    for tc, val in target_cols.items():
                        ws.cell(row=r, column=tc, value=val)
                    n += 1
            applied.append({"op": kind, "sheet": sheet_name,
                            "rows_affected": n})
        elif kind == "set_format":
            fmt = _NUMBER_FORMATS.get(op.get("format") or "")
            if not fmt:
                raise ValueError(
                    f"format must be one of {sorted(_NUMBER_FORMATS)}")
            n = 0
            for cname in op.get("columns") or []:
                ci = hdr.get(str(cname).strip().lower())
                if ci is None:
                    raise ValueError(
                        f"column '{cname}' not found — columns: {sorted(hdr)}")
                for r in range(2, ws.max_row + 1):
                    ws.cell(row=r, column=ci).number_format = fmt
                    n += 1
            applied.append({"op": kind, "sheet": sheet_name,
                            "rows_affected": n})
        else:
            raise ValueError(
                f"unknown op '{kind}' — use append_rows|add_column|"
                f"update_cells|add_sheet|rename_sheet|delete_sheet|set_format")
    return applied


def tool_xlsx_edit(args: dict) -> str:
    import brain as _brain
    import openpyxl
    from engine.tools.file_tools import (_enforce_artifact_path,
                                         _resolve_default_style,
                                         _load_doc_style)
    raw_path = (args.get("path") or "").strip()
    if not raw_path:
        return _err("xlsx_edit: 'path' is required")
    spec = args.get("spec")
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError as e:
            return _err(f"xlsx_edit: spec is not valid JSON: {e}")
    ops = (spec or {}).get("ops") if isinstance(spec, dict) else None
    if not ops:
        return _err('xlsx_edit: spec needs {"ops": [{"op": "...", ...}]}')
    # Edits write the file back — same artifact-folder rule as every write
    # tool. Attachments under /tmp/brain-attachments are read-only inputs; to
    # modify one, the model should xlsx_create a new file from it instead.
    path, perr = _enforce_artifact_path(raw_path, "xlsx_edit")
    if perr:
        return perr
    if not os.path.isfile(path):
        return _err(f"xlsx_edit: File not found: {raw_path} — xlsx_edit "
                    f"changes an EXISTING workbook in your artifact folder; "
                    f"use xlsx_create for new files.")
    try:
        # data_only=False preserves formulas on save (openpyxl drops cached
        # values; Excel recalculates on open — the standard trade).
        wb = openpyxl.load_workbook(
            path, keep_vba=path.lower().endswith(".xlsm"))
        kit = _style_kit(_load_doc_style(_resolve_default_style("")))
        applied = _apply_edit_ops(wb, ops, kit)
        wb.save(path)
    except (ValueError, FileNotFoundError) as e:
        return _err(f"xlsx_edit: {e}")
    except Exception as e:
        return _err(f"xlsx_edit: {type(e).__name__}: {e}")
    agent = get_request_context().current_agent
    _brain._after_file_write(path, "modified",
                             agent.agent_id if agent else "main")
    return _ok({"path": path, "ops_applied": applied, "status": "edited"})
