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
import json
import os
import re

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
# MSSQL has NO session-level read-only (only db-wide or via grants), so layer
# 2 does not exist there — the note must say so honestly (E4,
# DATA_SOURCES_V2_PLAN.md).
_DB_READONLY_NOTE_MSSQL = (
    "statement gate only (layer 1) — MSSQL has no session read-only; "
    "operational requirement: the configured login must hold db_datareader "
    "only (layer 3)")
_DB_RW_NOTE = ("rw source — DML allowed, DDL blocked; the configured DB "
               "user's grants are the last instance")

MSSQL_DEFAULT_ODBC_DRIVER = "ODBC Driver 17 for SQL Server"

# context_preview (DATA_SOURCES_V2 Phase 8, E13 — data minimisation by
# design): how much of a result may enter the LLM context. Per-source knob
# (`context_preview: none|head|full`, default head = today's 50 rows);
# `none` = schema + row_count only, not a single raw row (for sensitive
# sources this HARD-enforces the export-once-aggregate-locally chain:
# db_query(out='x.parquet') → data_query → small aggregates). The tool
# parameter `preview` may only TIGHTEN the source default, never loosen it.
_PREVIEW_RANK = {"none": 0, "head": 1, "full": 2}
DB_FULL_DISPLAY_ROWS = 1000  # 'full' preview cap (still bounded)
_PREVIEW_NONE_NOTE = (
    "context_preview=none — no data rows enter the conversation; pass "
    "out='name.parquet' and analyze locally with data_query (aggregates/"
    "joins), or out='name.csv' for xlsx tooling")


def _effective_preview(src: dict, args: dict) -> str:
    s = (src.get("context_preview") or "head").strip().lower()
    if s not in _PREVIEW_RANK:
        s = "head"
    p = (args.get("preview") or "").strip().lower()
    if p in _PREVIEW_RANK and _PREVIEW_RANK[p] < _PREVIEW_RANK[s]:
        return p
    return s


def _write_parquet(out_path: str, headers: list, rows: list) -> None:
    """Columnar export for the local-analysis chain (E13): the extract is
    pulled ONCE and lands as a Parquet artifact; every follow-up runs
    server-side via data_query. Type inference per column with a per-column
    string fallback (Decimal/date/datetime map natively via pyarrow)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    cols = list(zip(*rows)) if rows else [tuple()] * len(headers)
    arrays = []
    for i in range(len(headers)):
        vals = list(cols[i]) if i < len(cols) else []
        try:
            arrays.append(pa.array(vals))
        except (pa.ArrowInvalid, pa.ArrowTypeError,
                pa.ArrowNotImplementedError):
            arrays.append(pa.array(
                [None if v is None else str(v) for v in vals]))
    pq.write_table(
        pa.Table.from_arrays(arrays, names=[str(h) for h in headers]),
        out_path)

# access_mode (E5): "ro" (default) keeps today's SELECT/WITH-only contract;
# "rw" additionally admits DML. DDL stays blocked even on rw (O3) — schema
# changes by the agent are a different risk level; the DB grants of the
# configured user remain the last instance either way.
_RW_ALLOWED_KEYWORDS = ("select", "with", "insert", "update", "delete",
                        "merge")
_DDL_KEYWORDS = ("create", "alter", "drop", "truncate", "grant", "revoke")
_WRITE_KEYWORDS = ("insert", "update", "delete", "merge")


def _source_access_mode(src: dict) -> str:
    return "rw" if (src.get("access_mode") or "").strip().lower() == "rw" \
        else "ro"


def _check_tables_allowed(sql: str, stype: str, allowed: list) -> str | None:
    """E6: hard per-context table whitelist via sqlglot. Whitelist entries
    match case-insensitively, `schema.table` and bare `table` both ways.
    `information_schema.*` (and mssql `sys.*`) stay ALWAYS readable — schema
    exploration is the documented working path; that metadata of unlisted
    tables stays visible is a deliberate, documented limit (O2). CTE names
    are not table refs. Unparsable SQL → fail-CLOSED."""
    import sqlglot
    from sqlglot import exp
    dialect = "tsql" if stype == "mssql" else "postgres"
    allowed_norm = set()
    for a in allowed:
        a = (str(a) or "").strip().lower()
        if a:
            allowed_norm.add(a)
            allowed_norm.add(a.split(".")[-1])
    allowed_msg = ", ".join(sorted(a for a in allowed_norm if "." not in a))
    try:
        tree = sqlglot.parse_one(sql, dialect=dialect)
    except Exception as e:
        return (f"could not parse the SQL to enforce the table whitelist "
                f"({type(e).__name__}) — rewrite it plainly; allowed tables: "
                f"{allowed_msg}")
    ctes = set()
    for c in tree.find_all(exp.CTE):
        ctes.add((c.alias_or_name or "").lower())
    bad = []
    for t in tree.find_all(exp.Table):
        name = (t.name or "").lower()
        schema = (t.db or "").lower()
        if not name:
            continue
        if schema == "information_schema" or \
                (stype == "mssql" and schema == "sys"):
            continue
        if not schema and name in ctes:
            continue
        full = f"{schema}.{name}" if schema else name
        if full in allowed_norm or name in allowed_norm:
            continue
        bad.append(full)
    if bad:
        return (f"table(s) not allowed in this context: "
                f"{', '.join(sorted(set(bad)))} — allowed: {allowed_msg} "
                f"(information_schema stays readable for exploration)")
    return None


def _first_keyword(sql: str) -> str:
    m = re.match(r"(?is)^\s*([a-z]+)\b", sql or "")
    return m.group(1).lower() if m else ""


def _check_statement_allowed(sql: str, mode: str) -> str | None:
    """Layer 1 per access_mode. ro = the shared SELECT/WITH check (unchanged,
    still what xlsx_query/data_query use — their ro semantics are not
    configurable); rw admits DML but never DDL. ONE statement in both modes.
    Returns an error message or None."""
    if mode != "rw":
        err = _check_select_only(sql)
        if err and _first_keyword(sql) in _WRITE_KEYWORDS + _DDL_KEYWORDS:
            return ("source is read-only — writes need an rw source "
                    "(admin: Einstellungen → Datenquellen, access_mode); "
                    "only SELECT/WITH is allowed here")
        return err
    body = (sql or "").strip().rstrip(";").strip()
    if not body:
        return "empty sql"
    if ";" in body:
        return ("only ONE statement is allowed — remove the ';' and send a "
                "single statement")
    kw = _first_keyword(body)
    if kw in _DDL_KEYWORDS:
        return (f"DDL ({kw.upper()}) is blocked even on rw sources — "
                f"schema changes are not available to the agent")
    if kw not in _RW_ALLOWED_KEYWORDS:
        return ("allowed on an rw source: "
                "SELECT/WITH/INSERT/UPDATE/DELETE/MERGE")
    return None


def _data_sources() -> list[dict]:
    import brain as _brain
    return _brain._server_config().get("data_sources") or []


# Access policy (v9.363.0): who may use db_query at all. Grants are ADDITIVE
# (role OR team OR user — the agent/model-permission philosophy: grant present
# => allowed, admin always bypasses the grant axes). `enabled` is the master
# switch and turns the feature off for EVERYONE, admins included. A missing
# config block means ADMINS ONLY — external warehouse credentials default
# closed, unlike file-based data_query which stays ungated. Edited via the
# admin GUI (Einstellungen → Datenquellen, POST /v1/data-sources).
DATA_ACCESS_DEFAULT_ROLES = ("admin",)


def data_access_allowed(user_id: str) -> tuple[bool, str]:
    """Check the db_query access policy for a user id.
    Returns (allowed, reason) — reason set on deny, for the tool error."""
    import brain as _brain
    pol = _brain._server_config().get("data_sources_access") or {}
    if not pol.get("enabled", True):
        return False, "data-source access is switched off globally"
    if user_id == "__system__":
        return True, ""
    if not user_id:
        return False, "no user is associated with this turn"
    try:
        from server_lib.auth import AuthDB
        user = AuthDB.get_user(user_id)
    except Exception as e:
        return False, f"user lookup failed: {e}"
    if not user:
        return False, "unknown user"
    if user.get("role") == "admin":
        return True, ""
    roles = pol.get("roles")
    if roles is None:
        roles = list(DATA_ACCESS_DEFAULT_ROLES)
    if user.get("role") in roles:
        return True, ""
    if user_id in (pol.get("users") or []):
        return True, ""
    granted_teams = set(pol.get("teams") or [])
    if granted_teams:
        try:
            member_of = {t["id"] for t in AuthDB.get_user_teams(user_id)}
        except Exception:
            member_of = set()
        if member_of & granted_teams:
            return True, ""
    return False, ("no data-source grant for this user (admin: Einstellungen "
                   "→ Datenquellen — grant by role, team, or user)")


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


def _mssql_odbc_conn_str(src: dict) -> str:
    """DSN URL (mssql://user:pass@host:port/db) → ODBC connection string,
    EXACTLY per the bank-verified specimen (DATA_SOURCES_V2_PLAN.md Anhang B):
    `SERVER=host,port` with a COMMA, and deliberately NO `Encrypt=` /
    `TrustServerCertificate=` — Driver 17's `Encrypt=no` default is what
    works against on-prem servers with self-signed certs. Do NOT "upgrade"
    to Driver 18 (its `Encrypt=yes` default breaks exactly there)."""
    from urllib.parse import unquote, urlsplit
    opts = src.get("options") or {}
    u = urlsplit(_source_dsn(src))
    database = (u.path or "").lstrip("/")
    if not u.hostname or not database:
        raise ValueError(
            f"source '{src.get('name')}': mssql DSN must look like "
            f"mssql://user:pass@host[:port]/database")
    driver = (opts.get("odbc_driver") or "").strip() or \
        MSSQL_DEFAULT_ODBC_DRIVER
    parts = [f"DRIVER={{{driver}}}",
             f"SERVER={u.hostname},{u.port or 1433}",  # comma, not colon
             f"DATABASE={database}"]
    if opts.get("windows_auth"):
        # Banknetz alternative — needs domain/Kerberos context on the
        # Brain host; SQL auth is the default path.
        parts.append("Trusted_Connection=yes")
    else:
        user = unquote(u.username or "")
        if not user:
            raise ValueError(
                f"source '{src.get('name')}': mssql DSN has no username — "
                f"supply credentials or set options.windows_auth")
        # Brace-wrap so a ';' or '}' in the secret stays one ODBC value.
        pwd = unquote(u.password or "")
        parts.append("UID={%s}" % user.replace("}", "}}"))
        parts.append("PWD={%s}" % pwd.replace("}", "}}"))
    return ";".join(parts)


def _connect_mssql(src: dict):
    """mssql branch of _connect_readonly (pyodbc + msodbcsql17 — the only
    stack verified inside the target bank network, plan decision 10)."""
    opts = src.get("options") or {}
    try:
        import pyodbc  # lazy — only mssql sources pay the import
    except ImportError:
        raise RuntimeError(
            "pyodbc is not installed in the server interpreter — "
            "pip3 install pyodbc --break-system-packages (plus msodbcsql17 "
            "via the microsoft/mssql-release brew tap)")
    conn_str = _mssql_odbc_conn_str(src)
    login_timeout = int(opts.get("connect_timeout") or DB_CONNECT_TIMEOUT_S)
    try:
        # pyodbc.connect(timeout=) is the LOGIN timeout only.
        conn = pyodbc.connect(conn_str, timeout=login_timeout)
    except pyodbc.Error as e:
        if "IM002" in str(e):  # driver not found / wrong name
            raise RuntimeError(
                f"mssql: ODBC driver not found — configured "
                f"'{(opts.get('odbc_driver') or MSSQL_DEFAULT_ODBC_DRIVER)}', "
                f"installed: {pyodbc.drivers() or ['(none)']}. Install via "
                f"brew tap microsoft/mssql-release + HOMEBREW_ACCEPT_EULA=Y "
                f"brew install msodbcsql17, or set options.odbc_driver. "
                f"({e})")
        raise
    # QUERY timeout is a separate knob, set after connect (specimen: 30/60).
    timeout_ms = int(opts.get("statement_timeout_ms")
                     or DB_QUERY_STATEMENT_TIMEOUT_MS)
    conn.timeout = max(1, timeout_ms // 1000)
    # NO session read-only here (E4): layer 1 (statement gate) + layer 3
    # (db_datareader-only login) carry it. Plain cursor — pyodbc already
    # streams via fetchmany, named cursors don't exist.
    return conn, conn.cursor()


def _connect_readonly(src: dict, mode: str = "ro"):
    """Connect per source type; in ro mode with layer-2 read-only enforcement
    where the driver supports it. Returns (conn, exec_cursor) — the ro
    postgres cursor streams (server-side) so a huge SELECT never materialises
    client-side."""
    stype = (src.get("type") or "postgres").strip().lower()
    opts = src.get("options") or {}
    if stype == "mssql":
        return _connect_mssql(src)
    if stype == "rest":
        raise ValueError(
            f"source '{src.get('name')}' is a REST source — use rest_query")
    if stype != "postgres":
        # Deliberate: only wire what we can validate. Adding snowflake/oracle
        # is one isolated branch here once a real DSN exists to test against.
        raise ValueError(
            f"source type '{stype}' is not wired yet — supported: postgres, "
            f"mssql. (The read-only-grant requirement will apply there too.)")
    try:
        import psycopg2  # lazy — only db_query users pay the import
    except ImportError:
        raise RuntimeError(
            "psycopg2 is not installed in the server interpreter — "
            "pip3 install psycopg2-binary --break-system-packages")
    conn = psycopg2.connect(
        _source_dsn(src),
        connect_timeout=int(opts.get("connect_timeout") or DB_CONNECT_TIMEOUT_S))
    if mode != "rw":
        # Layer 2: the SESSION refuses writes regardless of the user's grants.
        conn.set_session(readonly=True)
    setup = conn.cursor()
    timeout_ms = int(opts.get("statement_timeout_ms")
                     or DB_QUERY_STATEMENT_TIMEOUT_MS)
    setup.execute("SET statement_timeout = %s", (str(timeout_ms),))
    setup.close()
    if mode == "rw":
        # Plain cursor: psycopg2 named (server-side) cursors are SELECT-only.
        return conn, conn.cursor()
    # Named cursor = server-side: rows stream in fetchmany-sized batches.
    cur = conn.cursor(name="brain_db_query")
    return conn, cur


def tool_db_query(args: dict) -> str:
    import brain as _brain
    # Authoritative access gate (v9.363.0). Deliberately IN the tool, not a
    # per-user tool-list mutation: the tool set stays byte-identical across
    # users, so the warm-pool KV prefix is untouched. Reaches every dispatch
    # path (chat, scheduler, workflows, delegation).
    ctx = get_request_context()
    user_id = ctx.current_user_id or ""
    allowed, why = data_access_allowed(user_id)
    if not allowed:
        return _err(f"db_query: access denied — {why}")
    source = (args.get("source") or "").strip()
    if not source:
        return _err("db_query: 'source' is required (a configured "
                    "data_sources name)")
    # Guard order (E1): policy (WHO, above) → scope (WHAT/WHERE) → mode
    # (ro/rw) → tables. Scope = per-turn {name: [tables]} from the project
    # config / session selection (E8); no scope set = nothing usable —
    # deliberately no silent global fallback. __system__ keeps full access.
    scope_tables = None
    if user_id != "__system__":
        scope = ctx.data_source_scope
        if scope is None:
            return _err(
                "db_query: no data sources are enabled for this context — "
                "in a project, enable them under Projekt-Einstellungen → "
                "Datenquellen; in a plain chat, pick them in the right "
                "panel (Datenquellen). This is a configuration matter — "
                "do NOT retry.")
        if source not in scope:
            avail = ", ".join(sorted(scope)) or "(none)"
            return _err(
                f"db_query: source '{source}' is not enabled in this "
                f"context — enabled here: {avail}. Other sources need to "
                f"be added in the project settings / right panel first.")
        scope_tables = [t for t in (scope.get(source) or [])]
    sql = args.get("sql") or ""
    try:
        src = _resolve_db_source(source)
    except ValueError as e:
        return _err(f"db_query: {e}")
    mode = _source_access_mode(src)
    sel_err = _check_statement_allowed(sql, mode)
    if sel_err:
        return _err(f"db_query: {sel_err}")
    sql = sql.strip().rstrip(";")
    if scope_tables:  # [] = all tables of the source; non-empty = whitelist
        terr = _check_tables_allowed(
            sql, (src.get("type") or "postgres").strip().lower(),
            scope_tables)
        if terr:
            return _err(f"db_query: {terr}")
    conn = None
    try:
        conn, cur = _connect_readonly(src, mode)
    except (ValueError, RuntimeError) as e:
        return _err(f"db_query: {e}")
    except Exception as e:
        # Unreachable host, refused connection, auth failure — clean tool
        # error, never a turn abort.
        return _err(f"db_query: connection failed: {type(e).__name__}: {e}")
    try:
        try:
            cur.execute(sql)
            # NB: mode gate is load-bearing — the ro postgres cursor is a
            # NAMED cursor whose description stays None until the first
            # fetch; only rw (plain cursor) can mean "DML, no result set".
            if mode == "rw" and cur.description is None:
                affected = cur.rowcount
                conn.commit()
                return _ok({"source": source, "mode": "rw",
                            "rowcount": affected,
                            "result": f"OK — {affected} row(s) affected",
                            "note": _DB_RW_NOTE})
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
        if mode == "rw":
            # Covers INSERT/UPDATE ... RETURNING (has a result set but still
            # writes); a commit after a plain SELECT is a no-op.
            conn.commit()
        headers = [d[0] for d in cur.description or []]
        row_count = len(rows)
        preview = _effective_preview(src, args)
        out_info = None
        out_name = (args.get("out") or "").strip()
        if out_name:
            from engine.tools.file_tools import _enforce_artifact_path
            low = out_name.lower()
            if not (low.endswith(".csv") or low.endswith(".parquet")):
                out_name += ".csv"
                low = out_name.lower()
            out_path, perr = _enforce_artifact_path(out_name, "db_query")
            if perr:
                return perr
            if low.endswith(".parquet"):
                _write_parquet(out_path, headers, rows)
            else:
                with open(out_path, "w", newline="", encoding="utf-8") as f:
                    w = csv.writer(f, delimiter=";")
                    w.writerow(headers)
                    w.writerows(rows)
            agent = get_request_context().current_agent
            _brain._after_file_write(
                out_path, "created", agent.agent_id if agent else "main")
            out_info = {"path": out_path, "rows": row_count}
        if preview == "none":
            # E13: schema + counts only — nothing to anonymise, nothing to
            # leak. The chain (export → data_query aggregates) is the way.
            res = {"source": source, "row_count": row_count,
                   "columns": headers, "result": _PREVIEW_NONE_NOTE}
        else:
            show = (DB_FULL_DISPLAY_ROWS if preview == "full"
                    else QUERY_DISPLAY_ROWS)
            md = _markdown_table(headers, rows[:show])
            if row_count > show:
                md += (f"\n\n_({row_count:,} rows total, showing first "
                       f"{show} — pass out='name.parquet' (or .csv) to save "
                       f"the full result)_")
            md = _brain._gdpr_anon_tool_text(md, f"db_query:{source}")
            res = {"source": source, "row_count": row_count, "result": md}
        if mode == "rw":
            res["mode"] = "rw"
            res["note"] = _DB_RW_NOTE
        else:
            res["read_only"] = (
                _DB_READONLY_NOTE_MSSQL
                if (src.get("type") or "").strip().lower() == "mssql"
                else _DB_READONLY_NOTE)
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


# ---------------------------------------------------------------------------
# Quellen-Steckbrief (DATA_SOURCES_V2 Phase 7, E11/E12)
# ---------------------------------------------------------------------------
# Per-source usage knowledge (`guide: {md, skill?, auto_generated_at?}` on the
# data_sources entry): table/field semantics, join paths, proven queries, and
# for rw sources the binding persistence patterns. Delivered wire-only on the
# last user message when the source is in the turn scope (the Websuche seam —
# history/DB stay clean, system prompt stays byte-stable). Above the token cap
# the preamble degrades to a per-source one-liner pointing at use_skill
# (guide.skill) — never a bulk injection ([[feedback_prompt_bloat_regression]]).

DATA_GUIDE_MAX_TOKENS_DEFAULT = 4000
# md-only sources that exceed the cap get a truncated slice instead of a
# use_skill pointer (there is nothing to lazily load) — visible, not silent.
_GUIDE_TRUNC_CHARS = 2000


def _source_guide(src: dict) -> dict:
    g = src.get("guide") or {}
    if not isinstance(g, dict):
        g = {}
    return {"md": str(g.get("md") or "").strip(),
            "skill": str(g.get("skill") or "").strip()}


def _guide_source_head(src: dict) -> str:
    mode = "read/write" if _source_access_mode(src) == "rw" else "read-only"
    stype = (src.get("type") or "postgres").strip().lower()
    return f"## Quelle „{src.get('name')}\" ({stype}, {mode})"


def build_data_source_guide_preamble(scope) -> str:
    """Wire-only preamble carrying the guides of the sources in `scope`
    ({name: [...]} — the per-turn data_source_scope). Returns '' when no
    scoped source has a guide. Sizing (E11): if the combined guides fit
    under data_sources_guide_max_tokens (config, default 4000; ~4 chars per
    token) they are injected in full; above the cap each source contributes
    only a short line — use_skill('<guide.skill>') when a skill is set, else
    a truncated md slice with an explicit cut marker."""
    import brain as _brain
    if not scope:
        return ""
    entries = []  # (src, md, skill)
    for name in sorted(scope):
        src = next((s for s in _data_sources()
                    if (s.get("name") or "").strip() == name), None)
        if not src:
            continue
        g = _source_guide(src)
        if g["md"] or g["skill"]:
            entries.append((src, g["md"], g["skill"]))
    if not entries:
        return ""
    try:
        max_tokens = int(_brain._server_config().get(
            "data_sources_guide_max_tokens")
            or DATA_GUIDE_MAX_TOKENS_DEFAULT)
    except (TypeError, ValueError):
        max_tokens = DATA_GUIDE_MAX_TOKENS_DEFAULT
    total_est = sum(len(md) // 4 for _s, md, _sk in entries)
    lines = ["[DATENQUELLEN-STECKBRIEFE — Nutzungswissen zu den in diesem "
             "Kontext freigegebenen Datenquellen. Nutze es, statt Schema/"
             "Semantik per Abfrage neu zu ermitteln; bei read/write-Quellen "
             "sind die dokumentierten Persistier-Muster verbindlich.]"]
    small = total_est <= max_tokens
    for src, md, skill in entries:
        lines.append(_guide_source_head(src))
        if small and md:
            lines.append(md)
            if skill:
                lines.append(f"Zusätzliche Doku: use_skill('{skill}').")
        elif skill:
            # Large path (E11): knowledge comes lazily via the skill infra.
            lines.append(f"Umfangreiche Doku vorhanden — lade "
                         f"use_skill('{skill}') VOR der ersten Abfrage "
                         f"dieser Quelle.")
        elif len(md) <= _GUIDE_TRUNC_CHARS:
            lines.append(md)  # tiny md — the cap was tripped by the others
        else:
            lines.append(md[:_GUIDE_TRUNC_CHARS]
                         + "\n[… Steckbrief gekürzt (über dem "
                           "Injektions-Limit) — Admin: umfangreiche Doku "
                           "als Quellen-Skill (guide.skill) hinterlegen.]")
    return "\n\n".join(lines)


def generate_source_guide_md(src: dict) -> str:
    """Bootstrap a guide skeleton from the live schema (admin GUI button
    „Steckbrief generieren"): tables + columns/types + row estimates + FK
    paths as Markdown with description placeholders. DETERMINISTIC — no LLM;
    the admin curates afterwards (hand-maintained with an auto kick-start,
    like brain-agent-guide). REST sources get a path skeleton from
    allowed_paths (no discovery call — REST has no information_schema, O7)."""
    import datetime
    name = src.get("name") or "?"
    stype = (src.get("type") or "postgres").strip().lower()
    today = datetime.date.today().isoformat()
    head = (f"# Steckbrief: {name} ({stype})\n\n"
            f"_Automatisch generiert am {today} — Beschreibungen bitte "
            f"kuratieren; Platzhalter ersetzen._\n")
    if stype == "rest":
        lines = [head, "## Endpoints\n"]
        paths = [str(p) for p in (src.get("allowed_paths") or [])]
        if not paths:
            lines.append("_(keine allowed_paths konfiguriert — Endpoints "
                         "hier dokumentieren: Pfad, Parameter, "
                         "Response-Shape, Fehlersemantik)_")
        for p in paths:
            lines.append(f"### `GET {p}`\n- Zweck: _(beschreiben)_\n"
                         f"- Parameter: _(beschreiben)_\n"
                         f"- Response: _(beschreiben)_")
        return "\n".join(lines)
    if stype == "mssql":
        sql_tables = (
            "SELECT t.TABLE_NAME, ISNULL(p.rows, 0) FROM "
            "INFORMATION_SCHEMA.TABLES t LEFT JOIN (SELECT o.name AS tn,"
            " SUM(p.rows) AS rows FROM sys.objects o JOIN sys.partitions"
            " p ON p.object_id = o.object_id AND p.index_id IN (0, 1)"
            " GROUP BY o.name) p ON p.tn = t.TABLE_NAME"
            " WHERE t.TABLE_TYPE = 'BASE TABLE' ORDER BY t.TABLE_NAME")
        sql_cols = (
            "SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM"
            " INFORMATION_SCHEMA.COLUMNS ORDER BY TABLE_NAME,"
            " ORDINAL_POSITION")
        sql_fks = (
            "SELECT OBJECT_NAME(fk.parent_object_id),"
            " COL_NAME(fkc.parent_object_id, fkc.parent_column_id),"
            " OBJECT_NAME(fk.referenced_object_id),"
            " COL_NAME(fkc.referenced_object_id, fkc.referenced_column_id)"
            " FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc"
            " ON fkc.constraint_object_id = fk.object_id")
    else:
        sql_tables = (
            "SELECT c.relname, GREATEST(c.reltuples, 0)::bigint"
            " FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE c.relkind = 'r' AND n.nspname NOT IN"
            " ('pg_catalog', 'information_schema') ORDER BY c.relname")
        sql_cols = (
            "SELECT table_name, column_name, data_type FROM"
            " information_schema.columns WHERE table_schema NOT IN"
            " ('pg_catalog', 'information_schema')"
            " ORDER BY table_name, ordinal_position")
        sql_fks = (
            "SELECT kcu.table_name, kcu.column_name, ccu.table_name,"
            " ccu.column_name FROM information_schema.table_constraints tc"
            " JOIN information_schema.key_column_usage kcu"
            " ON kcu.constraint_name = tc.constraint_name"
            " JOIN information_schema.constraint_column_usage ccu"
            " ON ccu.constraint_name = tc.constraint_name"
            " WHERE tc.constraint_type = 'FOREIGN KEY'")
    probe = dict(src)
    probe["options"] = dict(src.get("options") or {})
    probe["options"].setdefault("connect_timeout", 5)
    # NB: the ro postgres exec cursor from _connect_readonly is NAMED (single
    # use) — metadata queries run on fresh plain cursors off the same
    # (session-read-only) connection instead.
    conn, _exec_cur = _connect_readonly(probe)
    try:
        cur = conn.cursor()
        cur.execute(sql_tables)
        tables = [(r[0], int(r[1] or 0)) for r in cur.fetchall()]
        cols: dict = {}
        cur = conn.cursor()
        cur.execute(sql_cols)
        for tn, cn, dt in cur.fetchall():
            cols.setdefault(tn, []).append((cn, dt))
        fks = []
        try:
            cur = conn.cursor()
            cur.execute(sql_fks)
            fks = [tuple(r) for r in cur.fetchall()]
        except Exception:
            fks = []  # FK metadata is nice-to-have, never fails the bootstrap
    finally:
        try:
            conn.close()
        except Exception:
            pass
    lines = [head]
    for tn, nrows in tables:
        lines.append(f"## Tabelle `{tn}` (~{nrows:,} Zeilen)".replace(",", "."))
        lines.append("| Spalte | Typ | Beschreibung |\n|---|---|---|")
        for cn, dt in cols.get(tn, []):
            lines.append(f"| {cn} | {dt} | _(beschreiben)_ |")
        tfks = [f for f in fks if f[0] == tn]
        if tfks:
            lines.append("\nJoins: " + "; ".join(
                f"`{t}.{c}` → `{rt}.{rc}`" for t, c, rt, rc in tfks))
        lines.append("")
    if not tables:
        lines.append("_(keine Tabellen gefunden)_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# rest_query — REST connector (DATA_SOURCES_V2 Phase 6, E10)
# ---------------------------------------------------------------------------
# A REST source is an admin-configured BASE URL — rest_query can ONLY reach
# paths under it (no absolute URLs, no scheme switch, no '..' — SSRF is
# structurally excluded; redirects are NOT followed for the same reason).
# Same WHO axis (data_access_allowed), same WHAT axis (data_source_scope) as
# db_query — for REST sources the scope resources are PATH PREFIXES instead
# of tables. access_mode: ro = GET/HEAD only; rw adds POST/PUT/PATCH/DELETE.

REST_DEFAULT_TIMEOUT_S = 30
REST_DEFAULT_MAX_RESPONSE_KB = 256
REST_HARD_DOWNLOAD_CAP = 20 * 1024 * 1024  # sanity bound on the raw body
_REST_WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_REST_READ_METHODS = ("GET", "HEAD")


def _rest_validate_path(path: str) -> str | None:
    """Confine the request to the configured base_url. Checked on the RAW and
    the percent-decoded form (an encoded '..' must not slip past an
    allowed_paths prefix check)."""
    from urllib.parse import unquote
    p = (path or "").strip()
    if not p.startswith("/"):
        return "path must start with '/' (a path under the configured base_url)"
    if p.startswith("//"):
        return "'//…' is not allowed — only paths on the configured base_url"
    for probe in (p, unquote(p)):
        if "://" in probe:
            return ("absolute URLs are not allowed — rest_query only reaches "
                    "the configured base_url")
        if ".." in probe:
            return "'..' is not allowed in the path"
    return None


def _rest_auth_headers(src: dict) -> dict:
    """Build auth headers per source config: {kind: none|bearer|header|basic,
    secret|env_key, header_name?}. Secret resolution mirrors _source_dsn."""
    auth = src.get("auth") or {}
    kind = (auth.get("kind") or "none").strip().lower()
    if kind == "none":
        return {}
    secret = (auth.get("secret") or "").strip()
    if not secret and auth.get("env_key"):
        secret = (os.environ.get(auth["env_key"]) or "").strip()
    if not secret:
        raise ValueError(
            f"source '{src.get('name')}': auth kind '{kind}' but neither "
            f"auth.secret nor auth.env_key is set")
    if kind == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if kind == "header":
        return {(auth.get("header_name") or "X-API-Key").strip(): secret}
    if kind == "basic":
        import base64
        return {"Authorization": "Basic "
                + base64.b64encode(secret.encode()).decode()}
    raise ValueError(f"source '{src.get('name')}': unknown auth kind '{kind}'"
                     f" — supported: none, bearer, header, basic")


def _rest_flatten_csv(data) -> tuple[list, list]:
    """JSON array → (headers, rows), best effort: dict rows contribute their
    keys (first-seen order), nested values are JSON-encoded, scalars land in
    a single 'value' column."""
    headers, rows = [], []
    for item in data:
        if isinstance(item, dict):
            for k in item:
                if k not in headers:
                    headers.append(k)
        else:
            if "value" not in headers:
                headers.append("value")
    for item in data:
        if isinstance(item, dict):
            rows.append([
                (json.dumps(item.get(h), ensure_ascii=False)
                 if isinstance(item.get(h), (dict, list))
                 else item.get(h)) for h in headers])
        else:
            rows.append([item] + [None] * (len(headers) - 1))
    return headers, rows


def tool_rest_query(args: dict) -> str:
    import brain as _brain
    ctx = get_request_context()
    user_id = ctx.current_user_id or ""
    allowed, why = data_access_allowed(user_id)
    if not allowed:
        return _err(f"rest_query: access denied — {why}")
    source = (args.get("source") or "").strip()
    if not source:
        return _err("rest_query: 'source' is required (a configured "
                    "data_sources name of type rest)")
    # Same guard order as db_query (E1): policy → scope → mode → resource.
    scope_paths = None
    if user_id != "__system__":
        scope = ctx.data_source_scope
        if scope is None:
            return _err(
                "rest_query: no data sources are enabled for this context — "
                "in a project, enable them under Projekt-Einstellungen → "
                "Datenquellen; in a plain chat, pick them in the right "
                "panel (Datenquellen). This is a configuration matter — "
                "do NOT retry.")
        if source not in scope:
            avail = ", ".join(sorted(scope)) or "(none)"
            return _err(
                f"rest_query: source '{source}' is not enabled in this "
                f"context — enabled here: {avail}.")
        scope_paths = [str(p) for p in (scope.get(source) or [])]
    try:
        src = _resolve_db_source(source)
    except ValueError as e:
        return _err(f"rest_query: {e}")
    if (src.get("type") or "").strip().lower() != "rest":
        return _err(f"rest_query: source '{source}' is type "
                    f"'{src.get('type')}' — use db_query for SQL sources")
    mode = _source_access_mode(src)
    method = (args.get("method") or "GET").strip().upper()
    if method not in _REST_READ_METHODS + _REST_WRITE_METHODS:
        return _err(f"rest_query: unsupported method '{method}'")
    if mode != "rw" and method in _REST_WRITE_METHODS:
        return _err("rest_query: source is read-only — writes (POST/PUT/"
                    "PATCH/DELETE) need an rw source (admin: Einstellungen "
                    "→ Datenquellen, access_mode)")
    path = (args.get("path") or "/").strip()
    perr = _rest_validate_path(path)
    if perr:
        return _err(f"rest_query: {perr}")
    src_allowed = [str(p) for p in (src.get("allowed_paths") or [])]
    if src_allowed and not any(path.startswith(p) for p in src_allowed):
        return _err(f"rest_query: path '{path}' is outside the source's "
                    f"allowed paths: {', '.join(src_allowed)}")
    if scope_paths and not any(path.startswith(p) for p in scope_paths):
        return _err(f"rest_query: path '{path}' is outside this context's "
                    f"allowed paths: {', '.join(scope_paths)}")
    base = (src.get("base_url") or "").strip().rstrip("/")
    if not base.lower().startswith(("http://", "https://")):
        return _err(f"rest_query: source '{source}' has no valid base_url "
                    f"(admin: Einstellungen → Datenquellen)")
    try:
        headers = _rest_auth_headers(src)
    except ValueError as e:
        return _err(f"rest_query: {e}")
    opts = src.get("options") or {}
    timeout_s = float(opts.get("timeout_s") or REST_DEFAULT_TIMEOUT_S)
    max_kb = int(opts.get("max_response_kb") or REST_DEFAULT_MAX_RESPONSE_KB)
    params = args.get("params") if isinstance(args.get("params"), dict) else None
    body = args.get("body")
    import httpx
    try:
        # follow_redirects=False on purpose: a redirect to another host would
        # escape the base_url confinement. The 3xx lands in the result.
        resp = httpx.request(
            method, base + path, params=params,
            json=body if (body is not None
                          and method in _REST_WRITE_METHODS) else None,
            headers=headers, timeout=timeout_s, follow_redirects=False)
    except httpx.TimeoutException:
        return _err(f"rest_query: timeout after {timeout_s:.0f}s "
                    f"({method} {path})")
    except Exception as e:
        return _err(f"rest_query: request failed: {type(e).__name__}: {e}")
    raw = resp.content[:REST_HARD_DOWNLOAD_CAP]
    ctype = (resp.headers.get("content-type") or "").split(";")[0].strip()
    parsed = None
    if "json" in ctype or (raw[:1] in (b"{", b"[")):
        try:
            parsed = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            parsed = None
    text = (json.dumps(parsed, ensure_ascii=False, indent=2)
            if parsed is not None else raw.decode("utf-8", "replace"))
    cap = max_kb * 1024
    truncated = len(text) > cap
    shown = text[:cap]
    out_info = None
    out_name = (args.get("out") or "").strip()
    if out_name:
        from engine.tools.file_tools import _enforce_artifact_path
        low = out_name.lower()
        if not (low.endswith(".json") or low.endswith(".csv")):
            out_name += ".json"
            low = out_name.lower()
        out_path, aerr = _enforce_artifact_path(out_name, "rest_query")
        if aerr:
            return aerr
        if low.endswith(".csv"):
            data = parsed if isinstance(parsed, list) else None
            if data is None and isinstance(parsed, dict):
                # common wrapper shapes: take the first list value
                data = next((v for v in parsed.values()
                             if isinstance(v, list)), None)
            if data is None:
                return _err("rest_query: out=.csv needs a JSON array "
                            "response — use out=.json for this payload")
            hdrs, rows = _rest_flatten_csv(data)
            with open(out_path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(hdrs)
                w.writerows(rows)
        else:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)
        agent = ctx.current_agent
        _brain._after_file_write(
            out_path, "created", agent.agent_id if agent else "main")
        out_info = {"path": out_path}
    preview = _effective_preview(src, args)
    if preview == "none" and resp.status_code < 400:
        # E13: no payload content enters the context (error bodies still
        # surface — they are diagnostics, not data).
        res = {"source": source, "status": resp.status_code, "method": method,
               "path": path, "bytes": len(raw), "content_type": ctype,
               "result": ("context_preview=none — no response content "
                          "enters the conversation; pass out='name.json' "
                          "(or .csv for arrays) and analyze locally "
                          "(xlsx_query reads JSON, data_query reads "
                          "csv/parquet)")}
    else:
        shown = _brain._gdpr_anon_tool_text(shown, f"rest_query:{source}")
        res = {"source": source, "status": resp.status_code, "method": method,
               "path": path, "result": shown}
        if truncated:
            res["truncated"] = (f"response capped at {max_kb} KB — pass "
                                f"out='name.json' to save the full payload")
    if mode == "rw":
        res["mode"] = "rw"
    if resp.status_code >= 400:
        res["note"] = "HTTP error status — the body excerpt above is the server's error response"
    if out_info:
        res["saved"] = out_info
    return _ok(res)
