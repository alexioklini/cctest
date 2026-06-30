"""engine/tools/sql_analysis.py — SQL-corpus analysis for the code-mode workspace.

Why this exists separately from the cbm code-index: cbm's tree-sitter SQL parser
only emits generic Variable/Function nodes and no table/join structure. Real-world
SQL — especially the IBM DB2/iSeries-over-OPENQUERY style (`OPENQUERY(linked_srv,
'… SELECT … FROM IWPBPRD/H000DTA/KD00 …')`) — is NOT standard SQL, so a strict
parser (sqlglot) parses <40% of it and recovers almost no tables (measured: 29 vs
1168 on a 882-query bank corpus). So the PRIMARY analyzer is regex-based: dialect-
tolerant, works on 100% of files incl. the OPENQUERY/DB2 strings and `.dbq` XML
exports. sqlglot is an OPTIONAL bonus layer (column lineage) on the subset that
parses cleanly — gated on HAVE_SQLGLOT, never required.

Output mirrors the Cypher analysis path ({columns, rows, fileCol, lineCol, barCol})
so the frontend reuses _codeAnalysisTable. Paths are repo-relative (frontend joins
working_dir for the editor jump).
"""
from __future__ import annotations
import os
import re
import collections

try:
    import sqlglot  # optional — column lineage bonus only
    HAVE_SQLGLOT = True
except ImportError:
    HAVE_SQLGLOT = False

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cbm-cache",
              ".brain-extracted", ".mypy_cache", ".pytest_cache", ".next", "dist",
              "build", ".cache", "target", ".gradle", ".idea", ".tox", ".ruff_cache"}
_SQL_EXTS = ("sql", "dbq")

# table ref after FROM/JOIN: lib/schema/table, [db].[sch].[tbl], or plain — the
# leaf (last /- or .-separated part) is the table name. Tolerates IBM `LIB/SCH/TBL`.
_REF = r"([A-Za-z_\[][\w.\[\]$/]*)"
_TBL = re.compile(rf"\b(?:FROM|JOIN)\s+{_REF}", re.I)
_OQ = re.compile(r"OPENQUERY\s*\(\s*([A-Za-z_]\w*)", re.I)
_PROC = re.compile(r"\bCREATE\s+(?:OR\s+ALTER\s+)?PROC\w*\s+([\w.\[\]]+)", re.I)
_VIEW = re.compile(r"\bCREATE\s+VIEW\s+([\w.\[\]]+)", re.I)
_JOIN = re.compile(r"\bJOIN\b", re.I)
_SELECT = re.compile(r"\bSELECT\b", re.I)
# CTE: `WITH x AS (SELECT …` or a follow-on `, y AS (SELECT …`. The name is the
# identifier right before `AS (SELECT`. Tolerant — fires on each CTE in a chain.
_CTE = re.compile(r"\b([A-Za-z_]\w*)\s+AS\s*\(\s*SELECT", re.I)
_NOISE = {"OPENQUERY", "SELECT", "VALUES", "DUAL"}


def _leaf(ref: str) -> str:
    return ref.split("/")[-1].split(".")[-1].strip("[]")


def _read(fp: str) -> str:
    try:
        return open(fp, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


def _walk_sql(root: str):
    """Yield (relpath, line_offset, sql_text). Whole-file for .sql; one unit per
    <Body> for .dbq (the IBM query-tool XML export), with the line offset of that
    body so a jump lands roughly right."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
            if ext not in _SQL_EXTS:
                continue
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, root)
            txt = _read(fp)
            if not txt:
                continue
            if ext == "dbq":
                for m in re.finditer(r"<Body>(.*?)</Body>", txt, re.S):
                    yield rel, txt[:m.start()].count("\n") + 1, m.group(1)
            else:
                yield rel, 1, txt


def project_has_sql(working_dir: str) -> bool:
    """Cheap check: does the project contain any .sql/.dbq file? (stops at the
    first hit). Used to decide whether to show SQL analysis cards."""
    if not working_dir:
        return False
    root = os.path.abspath(working_dir)
    if not os.path.isdir(root):
        return False
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
            if ext in _SQL_EXTS:
                return True
    return False


def _scan(working_dir: str) -> dict:
    """One pass over all SQL units → the raw aggregates every analysis derives from.
    Cheap enough (regex over text) to run per analysis request; no caching needed
    for the corpus sizes in play (~19 MB / <1 s)."""
    tables = collections.Counter()
    table_files = collections.defaultdict(set)
    servers = collections.Counter()
    procs = []   # (name, relpath, line)
    views = []   # (name, relpath, line)
    complexity = []  # (joins, selects, relpath, line)
    for rel, off, sql in _walk_sql(working_dir):
        joins = len(_JOIN.findall(sql))
        sels = len(_SELECT.findall(sql))
        complexity.append((joins, sels, rel, off))
        for m in _TBL.findall(sql):
            leaf = _leaf(m)
            if leaf and leaf.upper() not in _NOISE and not leaf.startswith("{"):
                tables[leaf] += 1
                table_files[leaf].add(rel)
        for s in _OQ.findall(sql):
            servers[s] += 1
        for p in _PROC.findall(sql):
            procs.append((p.strip("[]"), rel, off + sql[:sql.upper().find("CREATE")].count("\n")))
        for v in _VIEW.findall(sql):
            views.append((v.strip("[]"), rel, off))
    return {"tables": tables, "table_files": table_files, "servers": servers,
            "procs": procs, "views": views, "complexity": complexity}


# Analysis registry: id → (label, hint, builder). Each builder takes the scan dict
# and returns {columns, rows, fileCol, lineCol, barCol}. fileCol/lineCol/-1 drive
# the frontend's clickable-jump + the proportional bar.
def _a_tables(s):
    rows = [[name, cnt, len(s["table_files"][name])]
            for name, cnt in s["tables"].most_common(40)]
    return {"columns": ["Tabelle", "Referenzen", "in Abfragen"], "rows": rows,
            "fileCol": -1, "lineCol": -1, "barCol": 1}


def _a_complex(s):
    rows = [[os.path.basename(f), j, sel, f, ln]
            for j, sel, f, ln in sorted(s["complexity"], reverse=True)[:40] if j > 0]
    return {"columns": ["Abfrage", "Joins", "SELECTs", "Datei", "Zeile"], "rows": rows,
            "fileCol": 3, "lineCol": 4, "barCol": 1}


def _a_servers(s):
    rows = [[name, cnt] for name, cnt in s["servers"].most_common(40)]
    return {"columns": ["Linked Server", "Zugriffe"], "rows": rows,
            "fileCol": -1, "lineCol": -1, "barCol": 1}


def _a_procs(s):
    rows = ([["PROCEDURE", n, f, ln] for n, f, ln in sorted(s["procs"])]
            + [["VIEW", n, f, ln] for n, f, ln in sorted(s["views"])])
    return {"columns": ["Art", "Name", "Datei", "Zeile"], "rows": rows,
            "fileCol": 2, "lineCol": 3, "barCol": -1}


_SQL_ANALYSES = {
    "sql_tables":  ("Tabellen-Hotspots", "meistgenutzte Datenquellen", _a_tables),
    "sql_complex": ("Komplexeste Abfragen", "Review-/Refactoring-Kandidaten", _a_complex),
    "sql_servers": ("Linked-Server-Zugriffe", "externe Abhängigkeiten", _a_servers),
    "sql_objects": ("Prozeduren & Views", "Inventar mit Sprung", _a_procs),
}


def sql_analyses_meta() -> list:
    """Card metadata for the frontend (id/label/hint), in display order."""
    return [{"id": k, "label": v[0], "hint": v[1]} for k, v in _SQL_ANALYSES.items()]


def sql_analyze(analysis_id: str, working_dir: str) -> dict:
    """Run one SQL analysis → {columns, rows, fileCol, lineCol, barCol} (or error)."""
    a = _SQL_ANALYSES.get(analysis_id)
    if not a:
        return {"error": f"unbekannte SQL-Auswertung: {analysis_id}"}
    if not working_dir or not os.path.isdir(working_dir):
        return {"error": "kein Arbeitsverzeichnis"}
    try:
        scan = _scan(working_dir)
    except Exception as e:  # noqa: BLE001 — surface a clean message, never 500
        return {"error": f"SQL-Analyse fehlgeschlagen: {e}"}
    out = a[2](scan)
    out["sqlglot"] = HAVE_SQLGLOT
    return out


# ─── Per-file symbols for the code-index outline ─────────────────────────────
# cbm's tree-sitter SQL parser emits almost nothing on these flat query scripts,
# so the file-tree "Symbole" panel would be empty for SQL. This produces the
# outline symbols ourselves from the same tolerant regex layer: the procedures /
# views a file DEFINES, plus the tables it READS, the CTEs it declares, and the
# linked servers it reaches. Output shape matches code_outline's symbol dicts
# ({name, label, file, line, signature}); codebase_memory.code_outline merges it.
_SQL_SYM_MAX_PER_FILE = 80  # safety cap so one pathological file can't flood the tree

# mtime-fingerprint cache: per_file_state polls every 5s; re-scanning ~19 MB of
# SQL each time (~260 ms) is wasteful when nothing changed. Key = working_dir,
# value = (fingerprint, symbols). Fingerprint = count + max-mtime over .sql/.dbq.
_SYM_CACHE: dict = {}


def _sql_fingerprint(root: str) -> tuple:
    n = 0
    mx = 0.0
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
            if ext in _SQL_EXTS:
                n += 1
                try:
                    mx = max(mx, os.path.getmtime(os.path.join(dp, fn)))
                except OSError:
                    pass
    return (n, mx)


def sql_file_symbols(working_dir: str) -> list:
    """[{name, label, file (repo-rel), line, signature}] for every .sql/.dbq unit.
    Labels: Procedure / View / CTE / Table / LinkedServer. Deduped per file
    (a table joined 5× → one symbol). Tolerant + never raises (returns []).
    mtime-cached so repeated polls on an unchanged corpus are free."""
    if not working_dir or not os.path.isdir(working_dir):
        return []
    root_fp = os.path.abspath(working_dir)
    try:
        fp = _sql_fingerprint(root_fp)
        cached = _SYM_CACHE.get(root_fp)
        if cached and cached[0] == fp:
            return cached[1]
    except Exception:  # noqa: BLE001
        fp = None
    out: list = []
    try:
        units = list(_walk_sql(working_dir))
    except Exception:  # noqa: BLE001
        return []
    # group units by file so we can dedup + cap per file (a .dbq has one Body unit;
    # a .sql is one unit — but keep it general).
    by_file: dict = collections.defaultdict(list)
    for rel, off, sql in units:
        by_file[rel].append((off, sql))
    for rel, parts in by_file.items():
        seen: set = set()           # (label, name) dedup within the file
        file_syms: list = []

        def _add(label, name, line, sig=""):
            name = (name or "").strip()
            if not name or name.upper() in _NOISE:
                return
            key = (label, name.lower())
            if key in seen:
                return
            seen.add(key)
            file_syms.append({"name": name, "label": label, "file": rel,
                              "line": line, "signature": sig})

        for off, sql in parts:
            # DEFINITIONS (precise line via the match offset within the unit)
            for m in _PROC.finditer(sql):
                _add("Procedure", m.group(1).strip("[]"),
                     off + sql[:m.start()].count("\n"))
            for m in _VIEW.finditer(sql):
                _add("View", m.group(1).strip("[]"),
                     off + sql[:m.start()].count("\n"))
            for m in _CTE.finditer(sql):
                _add("CTE", m.group(1),
                     off + sql[:m.start()].count("\n"))
            # REFERENCES (no definition line — point at the unit start)
            for m in _TBL.finditer(sql):
                leaf = _leaf(m.group(1))
                if leaf and not leaf.startswith("{"):
                    _add("Table", leaf, off, sig="referenziert")
            for m in _OQ.finditer(sql):
                _add("LinkedServer", m.group(1), off, sig="OPENQUERY")
        # stable display order: definitions first, then refs; cap per file
        order = {"Procedure": 0, "View": 1, "CTE": 2, "LinkedServer": 3, "Table": 4}
        file_syms.sort(key=lambda s: (order.get(s["label"], 9), s["name"].lower()))
        out.extend(file_syms[:_SQL_SYM_MAX_PER_FILE])
    if fp is not None:
        _SYM_CACHE[root_fp] = (fp, out)
    return out
