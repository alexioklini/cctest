"""engine/tools/r_analysis.py — R-corpus analysis for the code-mode workspace.

Why this exists separately from the cbm code-index: the tree-sitter code-index
gives generic symbol nodes, but real-world R — especially the IFRS-9 ECL/PIT/TTC
style (Base-R, heavy global state, source()-chained helper files, often a helper
file DUPLICATED as `_2.R`) — needs domain-specific findings the index can't
surface: which functions are defined twice across files, the source() dependency
graph, the read/write data-flow (which CSV/xlsx each script touches), and the
global-state coupling that makes refactoring risky. So this is a regex-based
analyzer, dialect-tolerant, run server-side over the raw .R/.r corpus.

Output mirrors the Cypher/SQL analysis path ({columns, rows, fileCol, lineCol,
barCol}) so the frontend reuses _codeAnalysisTable. Paths in rows are repo-
relative (frontend joins working_dir for the editor jump).

Ported from notes/r_analyzer_v2_verified.py (a prototype verified against the
user's real IFRS-9 scripts), with two bug fixes: (a) call-count no longer goes
negative when a function is duplicated — calls = total `name(` occurrences minus
the number of definition sites, floored at 0; (b) the READ path-extraction
captures the first string-literal argument (the path) instead of truncating at
the first comma when the path is a quoted literal (e.g. read.csv2(path, sep=…)).
"""
from __future__ import annotations
import os
import re
import collections

_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cbm-cache",
              ".brain-extracted", ".mypy_cache", ".pytest_cache", ".next", "dist",
              "build", ".cache", "target", ".gradle", ".idea", ".tox", ".ruff_cache"}
_R_EXTS = ("r",)                 # the analyses focus on .R/.r
_R_DETECT_EXTS = ("r", "rmd")    # project_has_r also counts .Rmd/.rmd

# Function definition: `name <- function(args)` or `name = function(args)`.
RE_FUNC = re.compile(r"^\s*([A-Za-z.][\w.]*)\s*(?:<-|=)\s*function\s*\(([^)]*)\)", re.M)
# source(...) — quoted literal or a paste0()-built path.
RE_SRCARG = re.compile(r"source\s*\(\s*(.+?)\)", re.I | re.S)
# read.* call → capture everything up to the first comma/paren (path expression).
RE_READ = re.compile(
    r"\b(read\.csv2?|read_csv|fread|read\.table|readRDS|read_excel|read\.delim)\s*\(\s*([^,)]+)",
    re.I)
# write.* call → 2nd positional arg is usually the path.
RE_WRITE = re.compile(
    r"\b(write\.csv2?|write\.table|write_csv|fwrite|saveRDS|write\.xlsx)\s*\([^,]*,\s*([^,)]+)",
    re.I)
RE_LIB = re.compile(r"\b(?:library|require)\s*\(\s*['\"]?([A-Za-z.][\w.]*)", re.I)
# A leading quoted string literal inside a call arg (the path, when present).
RE_QPATH = re.compile(r"^['\"]([^'\"]+)['\"]")
# Top-level assignment: `name <- …` / `name = …` at column 0 (no leading space).
RE_TOPLEVEL = re.compile(r"^([A-Za-z.][\w.]*)\s*(?:<-|=)(?!=)", re.M)


def _read(fp: str) -> str:
    try:
        return open(fp, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""


def _path_arg(expr: str) -> str:
    """Extract the path from a read/write call argument. Prefer the quoted string
    literal (so `read.csv2("a/b.csv", sep=…)` yields `a/b.csv`, not the truncated
    `"a/b.csv"`); otherwise return the trimmed expression (paste0(...) etc.)."""
    expr = expr.strip()
    m = RE_QPATH.match(expr)
    if m:
        return m.group(1)[:90]
    return expr[:90]


def _walk_r(root: str):
    """Yield (relpath, text) for every .R/.r file under root (skipping noise dirs)."""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        return
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
            if ext not in _R_EXTS:
                continue
            fp = os.path.join(dp, fn)
            rel = os.path.relpath(fp, root)
            txt = _read(fp)
            if txt:
                yield rel, txt


def project_has_r(working_dir: str) -> bool:
    """Cheap check: does the project contain any .R/.r/.Rmd/.rmd file? (stops at
    the first hit). Used to decide whether to show R analysis cards."""
    if not working_dir:
        return False
    root = os.path.abspath(working_dir)
    if not os.path.isdir(root):
        return False
    for dp, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
            if ext in _R_DETECT_EXTS:
                return True
    return False


def _func_bodies(text: str):
    """Yield (name, line, nargs, body) for each top-level function def in one file.
    Body = from the def line up to (but not including) the next top-level def (a
    brace-balanced span is overkill for Base-R; the next top-level `name <- function`
    boundary is good enough for length + global-usage measurement)."""
    defs = list(RE_FUNC.finditer(text))
    for i, m in enumerate(defs):
        name = m.group(1)
        nargs = len([a for a in m.group(2).split(",") if a.strip()])
        line = text[:m.start()].count("\n") + 1
        end = defs[i + 1].start() if i + 1 < len(defs) else len(text)
        body = text[m.start():end]
        yield name, line, nargs, body


def _scan(working_dir: str) -> dict:
    """One pass over all R files → the raw aggregates every analysis derives from."""
    funcs = collections.defaultdict(list)   # name -> [(rel, line, nargs)]
    func_bodies = []                        # (name, rel, line, nlines, body)
    sources = []                            # (rel, sourced-target)
    reads = []                              # (rel, fn, path, line)
    writes = []                             # (rel, fn, path, line)
    libs = collections.Counter()
    globals_set = set()                     # top-level assigned names (across corpus)
    text = {}                               # rel -> text (for call-count pass)

    for rel, t in _walk_r(working_dir):
        text[rel] = t
        for name, line, nargs, body in _func_bodies(t):
            funcs[name].append((rel, line, nargs))
            func_bodies.append((name, rel, line, body.count("\n") + 1, body))
        for m in RE_SRCARG.finditer(t):
            arg = m.group(1).strip()
            tgt = re.findall(r"['\"]([^'\"]+\.[Rr])['\"]", arg)
            sources.append((rel, tgt[0] if tgt else arg[:80]))
        for m in RE_READ.finditer(t):
            ln = t[:m.start()].count("\n") + 1
            reads.append((rel, m.group(1), _path_arg(m.group(2)), ln))
        for m in RE_WRITE.finditer(t):
            ln = t[:m.start()].count("\n") + 1
            writes.append((rel, m.group(1), _path_arg(m.group(2)), ln))
        for lib in RE_LIB.findall(t):
            libs[lib] += 1
        for gm in RE_TOPLEVEL.findall(t):
            globals_set.add(gm)

    # duplicate functions: same name defined in >1 file (the high-value finding —
    # e.g. ECL_Hilfsfunktion.R vs _2.R defining the same helpers).
    dups = {n: locs for n, locs in funcs.items() if len({l[0] for l in locs}) > 1}

    # call-frequency: total `name(` occurrences across the corpus minus the number
    # of definition sites (floored at 0). FIX vs prototype: never goes negative for
    # a duplicated function (the prototype subtracted len(defs), which over-counts
    # when a name appears with the same call-pattern at each def line).
    callcount = collections.Counter()
    for n in funcs:
        total = sum(len(re.findall(rf"(?<![\w.]){re.escape(n)}\s*\(", t)) for t in text.values())
        callcount[n] = max(0, total - len(funcs[n]))

    return {"funcs": funcs, "func_bodies": func_bodies, "dups": dups,
            "sources": sources, "reads": reads, "writes": writes, "libs": libs,
            "callcount": callcount, "globals": globals_set}


# Analysis registry: id → (label, hint, builder). Each builder takes the scan dict
# and returns {columns, rows, fileCol, lineCol, barCol}.
def _a_functions(s):
    """Every function with file/line/args/call-count; duplicates marked with ⚠."""
    rows = []
    for name in sorted(s["funcs"], key=lambda n: (-s["callcount"][n], n)):
        for rel, line, nargs in s["funcs"][name]:
            label = ("⚠ " + name) if name in s["dups"] else name
            rows.append([label, s["callcount"][name], nargs, rel, line])
    return {"columns": ["Funktion", "Aufrufe", "Args", "Datei", "Zeile"], "rows": rows,
            "fileCol": 3, "lineCol": 4, "barCol": 1}


def _a_dataflow(s):
    """READ/WRITE rows: direction, the call (read.csv2/write.xlsx/…), path, script,
    line. Shows which script reads/writes which data file."""
    rows = ([["READ", fn, path, rel, ln] for rel, fn, path, ln in s["reads"]]
            + [["WRITE", fn, path, rel, ln] for rel, fn, path, ln in s["writes"]])
    rows.sort(key=lambda r: (r[3], r[4]))
    return {"columns": ["Richtung", "Aufruf", "Pfad/Datei", "Skript", "Zeile"], "rows": rows,
            "fileCol": 3, "lineCol": 4, "barCol": -1}


def _a_sources(s):
    """source()-graph: script → sourced target. Zero rows if no source() calls."""
    rows = [[rel, "→", tgt] for rel, tgt in s["sources"]]
    return {"columns": ["Skript", "", "sourct"], "rows": rows,
            "fileCol": 0, "lineCol": -1, "barCol": -1}


def _a_globals(s):
    """Per function: its length in lines + how many top-level/global names it
    references in its body — surfaces the global-state coupling."""
    glb = s["globals"]
    rows = []
    for name, rel, line, nlines, body in s["func_bodies"]:
        used = set()
        for g in glb:
            if g == name:
                continue
            if re.search(rf"(?<![\w.]){re.escape(g)}(?![\w.])", body):
                used.add(g)
        rows.append([name, nlines, len(used), rel, line])
    rows.sort(key=lambda r: (-r[1], -r[2]))
    return {"columns": ["Funktion", "Zeilen", "nutzt Globals", "Datei", "Zeile"], "rows": rows,
            "fileCol": 3, "lineCol": 4, "barCol": 1}


_R_ANALYSES = {
    "r_functions": ("Funktionen & Aufrufe", "inkl. Duplikat-Warnung", _a_functions),
    "r_dataflow":  ("Daten-Fluss & Quellen", "welche Datei liest/schreibt was", _a_dataflow),
    "r_sources":   ("Skript-Abhängigkeiten", "source()-Graph", _a_sources),
    "r_globals":   ("Globaler Zustand & Komplexität", "Funktionen nach Größe + Global-Nutzung", _a_globals),
}


def r_analyses_meta() -> list:
    """Card metadata for the frontend (id/label/hint), in display order."""
    return [{"id": k, "label": v[0], "hint": v[1]} for k, v in _R_ANALYSES.items()]


def r_analyze(analysis_id: str, working_dir: str) -> dict:
    """Run one R analysis → {columns, rows, fileCol, lineCol, barCol} (or error)."""
    a = _R_ANALYSES.get(analysis_id)
    if not a:
        return {"error": f"unbekannte R-Auswertung: {analysis_id}"}
    if not working_dir or not os.path.isdir(working_dir):
        return {"error": "kein Arbeitsverzeichnis"}
    try:
        scan = _scan(working_dir)
    except Exception as e:  # noqa: BLE001 — surface a clean message, never 500
        return {"error": f"R-Analyse fehlgeschlagen: {e}"}
    return a[2](scan)
