"""data_query — ONE read-only SQL SELECT over Parquet/CSV/DuckDB files.

Quant-Workbench Phase D1 (QUANT_WORKBENCH_PLAN.md): the columnar sibling of
xlsx_query. Same principle — the model supplies only INTENT (a SELECT), the
server moves the data; bulk data never flows through the model. Unlike the
xlsx path (rows loaded into in-memory SQLite), DuckDB scans the files LAZILY
via views, so million-row Parquet aggregates run in milliseconds without
loading anything.

Read-only is enforced in three layers (the xlsx_query principle, translated —
DuckDB has no sqlite authorizer):
  (a) the shared SELECT/WITH prefix check + multi-statement reject
      (imported from xlsx_tools — single fix point, not copied),
  (b) sources are views on the files / READ_ONLY-attached databases, never
      writable tables,
  (c) engine-level lockdown: `allowed_paths` = exactly the input files,
      `enable_external_access=false`, then `lock_configuration=true` — COPY TO,
      reads of OTHER files and re-enabling are all PermissionErrors inside the
      SQL itself (validated live, incl. COPY TO an allowed input path).
Order matters: .duckdb files must be ATTACHed (READ_ONLY) BEFORE
enable_external_access=false (ATTACH touches WAL sidecars outside
allowed_paths); lazy parquet/csv views keep working after the lockdown
because their files are in allowed_paths.

Wired per the 4-site rule (TOOL_DEFINITIONS / TOOL_GROUPS / impl here /
TOOL_DISPATCH). Reaches brain runtime via lazy `import brain as _brain`.
"""

from __future__ import annotations

import csv
import os

from engine.context import get_request_context
from engine.tool_exec import _ok, _err
from engine.tools.xlsx_tools import (
    QUERY_DISPLAY_ROWS,
    _check_select_only,
    _markdown_table,
    _resolve_input_path,
    _sanitize_name,
)

_PARQUET_EXTS = {".parquet"}
_CSV_EXTS = {".csv", ".tsv"}
_DUCKDB_EXTS = {".duckdb"}
_DATA_EXTS = _PARQUET_EXTS | _CSV_EXTS | _DUCKDB_EXTS

# DuckDB streams the files (nothing is loaded into RAM up front), so the file
# cap is a sanity bound against absurd scans — deliberately far above
# xlsx_query's 30 MB, which exists because THAT path materialises rows in
# SQLite. The result cap mirrors xlsx_query's 200k: nothing bulk reaches the
# model either way (display is 50 rows, the rest goes to a CSV artifact).
DATA_MAX_FILE_MB = 512
DATA_MAX_RESULT_ROWS = 200_000


def _sql_quote(path: str) -> str:
    return path.replace("'", "''")


def _build_duckdb(paths: list[str]):
    """One :memory: DuckDB with a read-only view per file (per table for
    .duckdb files), locked down to exactly those files. Returns
    (conn, tables) — tables = [{view, file, columns: [(name, type)], rows}]."""
    import duckdb

    resolved = []
    for p in paths:
        rp = _resolve_input_path(p)
        if not os.path.exists(rp):
            raise FileNotFoundError(f"file not found: {p}")
        ext = os.path.splitext(rp)[1].lower()
        if ext not in _DATA_EXTS:
            hint = (" — for .xlsx/.json/.xml use xlsx_query"
                    if ext not in (".duckdb",) else "")
            raise ValueError(
                f"{os.path.basename(rp)}: unsupported type '{ext}' "
                f"(data_query takes .parquet/.csv/.tsv/.duckdb){hint}")
        size_mb = os.path.getsize(rp) / (1024 * 1024)
        if size_mb > DATA_MAX_FILE_MB:
            raise ValueError(
                f"{os.path.basename(rp)} is {size_mb:.0f} MB "
                f"(> {DATA_MAX_FILE_MB} MB) — split the file")
        resolved.append((rp, ext))

    conn = duckdb.connect(":memory:")
    try:
        # (1) READ_ONLY-attach .duckdb files BEFORE the lockdown (ATTACH needs
        # sidecar access that allowed_paths can't grant).
        attached = {}  # rp -> alias
        for i, (rp, ext) in enumerate(resolved):
            if ext in _DUCKDB_EXTS:
                alias = f"src{i}"
                conn.execute(f"ATTACH '{_sql_quote(rp)}' AS {alias} (READ_ONLY)")
                attached[rp] = alias
        # (2) Lockdown: only the lazy-scanned files stay reachable.
        lazy = [rp for rp, ext in resolved if ext not in _DUCKDB_EXTS]
        if lazy:
            conn.execute("SET allowed_paths = [{}]".format(
                ", ".join(f"'{_sql_quote(rp)}'" for rp in lazy)))
        conn.execute("SET enable_external_access = false")
        # (3) Views with sanitized names — the "never guess identifiers" lever.
        used: set = set()
        multi = len(resolved) > 1
        tables = []

        def _add_view(raw_name: str, select_from: str, rp: str):
            v = _sanitize_name(raw_name, used)
            conn.execute(f'CREATE VIEW "{v}" AS SELECT * FROM {select_from}')
            cols = conn.execute(f'DESCRIBE "{v}"').fetchall()
            n = conn.execute(f'SELECT COUNT(*) FROM "{v}"').fetchone()[0]
            tables.append({"view": v, "file": os.path.basename(rp),
                           "columns": [(c[0], c[1]) for c in cols], "rows": n})

        for rp, ext in resolved:
            stem = os.path.splitext(os.path.basename(rp))[0]
            if ext in _DUCKDB_EXTS:
                alias = attached[rp]
                tabs = conn.execute(
                    "SELECT table_name FROM duckdb_tables() "
                    "WHERE database_name = ?", [alias]).fetchall()
                if not tabs:
                    raise ValueError(
                        f"{os.path.basename(rp)}: no tables in database")
                for (tname,) in tabs:
                    raw = f"{stem}_{tname}" if multi else tname
                    _add_view(raw, f'{alias}."{tname}"', rp)
            elif ext in _PARQUET_EXTS:
                _add_view(stem, f"read_parquet('{_sql_quote(rp)}')", rp)
            else:  # csv/tsv — DuckDB sniffs delimiter/header/types itself
                _add_view(stem, f"read_csv('{_sql_quote(rp)}')", rp)
        # (4) Freeze: no SQL can re-enable external access from here on.
        conn.execute("SET lock_configuration = true")
        return conn, tables
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        raise


def _schema_echo(tables: list[dict]) -> str:
    lines = ["Views for data_query:"]
    for t in tables:
        cols = ", ".join(f"{c} {typ}" for c, typ in t["columns"])
        lines.append(f"- {t['view']}({cols}) — {t['rows']:,} rows [{t['file']}]")
    return "\n".join(lines)


def tool_data_query(args: dict) -> str:
    import brain as _brain
    paths = args.get("paths") or ([args["path"]] if args.get("path") else [])
    if not paths:
        return _err("data_query: 'path' (or 'paths') is required")
    sql = args.get("sql") or ""
    sel_err = _check_select_only(sql)
    if sel_err:
        return _err(f"data_query: {sel_err}")
    sql = sql.strip().rstrip(";")
    try:
        conn, tables = _build_duckdb(paths)
    except (FileNotFoundError, ValueError) as e:
        return _err(f"data_query: {e}")
    except Exception as e:
        return _err(f"data_query: {type(e).__name__}: {e}")
    try:
        import duckdb
        try:
            cur = conn.execute(sql)
            # +1 row detects overflow without fetching the whole overshoot.
            rows = cur.fetchmany(DATA_MAX_RESULT_ROWS + 1)
        except duckdb.Error as e:
            # Self-correction loop: echo the real schema so the model fixes
            # identifiers in one round (there is no data_inspect on purpose).
            return _err(f"data_query: SQL error: {e}\n\n{_schema_echo(tables)}")
        if len(rows) > DATA_MAX_RESULT_ROWS:
            return _err(
                f"data_query: result exceeds {DATA_MAX_RESULT_ROWS:,} rows — "
                f"aggregate or filter in SQL")
        headers = [d[0] for d in cur.description or []]
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
            out_path, perr = _enforce_artifact_path(out_name, "data_query")
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
        src = "data_query:" + ",".join(os.path.basename(p) for p in paths)
        md = _brain._gdpr_anon_tool_text(md, src)
        # Always list the views: data_query has no inspect counterpart, so the
        # first result doubles as the schema the model works from.
        res = {"row_count": row_count, "result": md,
               "views": [f"{t['view']} ({t['rows']:,} rows) [{t['file']}]"
                         for t in tables]}
        if out_info:
            res["saved"] = out_info
        return _ok(res)
    except Exception as e:
        return _err(f"data_query: {type(e).__name__}: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
