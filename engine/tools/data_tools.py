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


# ---------------------------------------------------------------------------
# db_query — warehouse connector (Quant-Workbench D2)
# ---------------------------------------------------------------------------
# Sources live in config.json → data_sources (gitignored, per-machine — the
# crawl4ai pattern): [{name, type, dsn|env_key, options?}]. Read-only again in
# three layers: (1) the shared SELECT/WITH prefix check, (2) session-level
# read-only where the driver API supports it (postgres:
# default_transaction_read_only via set_session), (3) OPERATIONAL REQUIREMENT
# documented in every result — the configured DB user MUST be a read-only
# grant; layers 1+2 are belt and braces on top of that, not a substitute.
# There is deliberately NO db_list_sources tool: the schema never names the
# per-machine sources; the model learns them from the error of a wrong guess.

DB_QUERY_STATEMENT_TIMEOUT_MS = 60_000
DB_CONNECT_TIMEOUT_S = 10
_DB_READONLY_NOTE = ("session read-only (layer 2); operational requirement: "
                     "the configured DB user must hold read-only grants only "
                     "(layer 3)")


def _data_sources() -> list[dict]:
    import brain as _brain
    return _brain._server_config().get("data_sources") or []


def _resolve_db_source(name: str) -> dict:
    srcs = _data_sources()
    for s in srcs:
        if (s.get("name") or "").strip() == name:
            return s
    avail = ", ".join(sorted((s.get("name") or "?") for s in srcs)) or \
        "(none configured — admin: config.json → data_sources)"
    raise ValueError(f"unknown source '{name}' — available: {avail}")


def _source_dsn(src: dict) -> str:
    dsn = (src.get("dsn") or "").strip()
    if not dsn and src.get("env_key"):
        dsn = (os.environ.get(src["env_key"]) or "").strip()
        if not dsn:
            raise ValueError(
                f"source '{src.get('name')}': env var '{src['env_key']}' "
                f"is not set (server environment)")
    if not dsn:
        raise ValueError(
            f"source '{src.get('name')}' has neither 'dsn' nor 'env_key'")
    return dsn


def _connect_readonly(src: dict):
    """Connect per source type with layer-2 read-only enforcement.
    Returns (conn, exec_cursor) — exec_cursor streams (server-side cursor)
    so a huge SELECT never materialises client-side."""
    stype = (src.get("type") or "postgres").strip().lower()
    opts = src.get("options") or {}
    if stype != "postgres":
        # Deliberate: only wire what we can validate (D2 success criteria run
        # against postgres). Adding snowflake/mssql/oracle is one isolated
        # branch here once a real DSN exists to test against.
        raise ValueError(
            f"source type '{stype}' is not wired yet — supported: postgres. "
            f"(The read-only-grant requirement will apply there too.)")
    try:
        import psycopg2  # lazy — only db_query users pay the import
    except ImportError:
        raise RuntimeError(
            "psycopg2 is not installed in the server interpreter — "
            "pip3 install psycopg2-binary --break-system-packages")
    conn = psycopg2.connect(
        _source_dsn(src),
        connect_timeout=int(opts.get("connect_timeout") or DB_CONNECT_TIMEOUT_S))
    # Layer 2: the SESSION refuses writes regardless of the user's grants.
    conn.set_session(readonly=True)
    setup = conn.cursor()
    timeout_ms = int(opts.get("statement_timeout_ms")
                     or DB_QUERY_STATEMENT_TIMEOUT_MS)
    setup.execute("SET statement_timeout = %s", (str(timeout_ms),))
    setup.close()
    # Named cursor = server-side: rows stream in fetchmany-sized batches.
    cur = conn.cursor(name="brain_db_query")
    return conn, cur


def tool_db_query(args: dict) -> str:
    import brain as _brain
    source = (args.get("source") or "").strip()
    if not source:
        return _err("db_query: 'source' is required (a configured "
                    "data_sources name)")
    sql = args.get("sql") or ""
    sel_err = _check_select_only(sql)
    if sel_err:
        return _err(f"db_query: {sel_err}")
    sql = sql.strip().rstrip(";")
    conn = None
    try:
        src = _resolve_db_source(source)
        conn, cur = _connect_readonly(src)
    except (ValueError, RuntimeError) as e:
        return _err(f"db_query: {e}")
    except Exception as e:
        # Unreachable host, refused connection, auth failure — clean tool
        # error, never a turn abort.
        return _err(f"db_query: connection failed: {type(e).__name__}: {e}")
    try:
        try:
            cur.execute(sql)
            rows = cur.fetchmany(DATA_MAX_RESULT_ROWS + 1)
        except Exception as e:
            # SQL errors carry the server's own hint text (unknown column
            # etc.); schema exploration is a SELECT on information_schema.
            return _err(f"db_query: {type(e).__name__}: "
                        f"{str(e).strip()}\n(Explore the schema with e.g. "
                        f"SELECT table_name FROM information_schema.tables "
                        f"WHERE table_schema='public')")
        if len(rows) > DATA_MAX_RESULT_ROWS:
            return _err(f"db_query: result exceeds {DATA_MAX_RESULT_ROWS:,} "
                        f"rows — aggregate or filter in SQL")
        headers = [d[0] for d in cur.description or []]
        row_count = len(rows)
        md = _markdown_table(headers, rows[:QUERY_DISPLAY_ROWS])
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
            out_path, perr = _enforce_artifact_path(out_name, "db_query")
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
        md = _brain._gdpr_anon_tool_text(md, f"db_query:{source}")
        res = {"source": source, "row_count": row_count, "result": md,
               "read_only": _DB_READONLY_NOTE}
        if out_info:
            res["saved"] = out_info
        return _ok(res)
    except Exception as e:
        return _err(f"db_query: {type(e).__name__}: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
