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
