"""Code Structure Graph — AST-based code graph (tree-sitter + SQLite).

Extracted from brain.py (refactor A2). Self-contained subsystem:
own thread-local SQLite pool (code-graph.db), tree-sitter AST parsing
(lazy/optional import), qualified names `{file_path}::{ClassName.method}`,
edges CALLS / IMPORTS_FROM / INHERITS / IMPLEMENTS / CONTAINS / TESTED_BY,
incremental rebuild via SHA-256 file-hash skip.

brain.py re-exports the public symbols so all callers (TOOL_DISPATCH
entries, `_after_file_write` -> `_maybe_update_code_graph`) still resolve.

Do NOT top-level `import brain` here (cycle). The one brain-runtime touch
(sidecar background_call for LLM summaries) is a lazy import inside the
method body.
"""

import datetime
import hashlib
import json
import os
import sqlite3
import threading

# Mirror brain.AGENTS_DIR path computation (module lives in engine/, so
# parent dir of this file's dir is the repo root that holds agents/).
AGENTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")


def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


CODE_GRAPH_DB = os.path.join(AGENTS_DIR, "main", "code-graph.db")

_code_graph_db_pool = threading.local()

_EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".go": "go", ".rs": "rust", ".java": "java", ".c": "c", ".cpp": "cpp",
    ".h": "c", ".hpp": "cpp", ".cs": "c_sharp", ".rb": "ruby",
    ".kt": "kotlin", ".swift": "swift", ".php": "php",
}

_DEFAULT_EXCLUDE_DIRS = {"node_modules", ".git", "__pycache__", "venv", ".venv", "dist", "build", ".next", ".tox", "egg-info"}

# AST node type mappings per language
_CLASS_TYPES = {
    "python": {"class_definition"},
    "javascript": {"class_declaration"},
    "typescript": {"class_declaration"},
    "tsx": {"class_declaration"},
    "go": {"type_declaration"},
    "rust": {"struct_item", "enum_item", "impl_item"},
    "java": {"class_declaration", "interface_declaration"},
    "c": {"struct_specifier"},
    "cpp": {"class_specifier", "struct_specifier"},
    "c_sharp": {"class_declaration", "interface_declaration"},
    "ruby": {"class", "module"},
    "kotlin": {"class_declaration", "interface_declaration"},
    "swift": {"class_declaration", "struct_declaration", "protocol_declaration"},
    "php": {"class_declaration", "interface_declaration"},
}

_FUNCTION_TYPES = {
    "python": {"function_definition"},
    "javascript": {"function_declaration", "method_definition", "arrow_function"},
    "typescript": {"function_declaration", "method_definition", "arrow_function"},
    "tsx": {"function_declaration", "method_definition", "arrow_function"},
    "go": {"function_declaration", "method_declaration"},
    "rust": {"function_item"},
    "java": {"method_declaration", "constructor_declaration"},
    "c": {"function_definition"},
    "cpp": {"function_definition"},
    "c_sharp": {"method_declaration", "constructor_declaration"},
    "ruby": {"method", "singleton_method"},
    "kotlin": {"function_declaration"},
    "swift": {"function_declaration"},
    "php": {"function_definition", "method_declaration"},
}

_IMPORT_TYPES = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "java": {"import_declaration"},
    "c": {"preproc_include"},
    "cpp": {"preproc_include"},
    "c_sharp": {"using_directive"},
    "ruby": {"call"},  # require/require_relative
    "kotlin": {"import_header"},
    "swift": {"import_declaration"},
    "php": {"namespace_use_declaration"},
}

_CALL_TYPES = {
    "python": {"call"},
    "javascript": {"call_expression"},
    "typescript": {"call_expression"},
    "tsx": {"call_expression"},
    "go": {"call_expression"},
    "rust": {"call_expression", "macro_invocation"},
    "java": {"method_invocation"},
    "c": {"call_expression"},
    "cpp": {"call_expression"},
    "c_sharp": {"invocation_expression"},
    "ruby": {"call"},
    "kotlin": {"call_expression"},
    "swift": {"call_expression"},
    "php": {"function_call_expression", "method_call_expression"},
}


def _code_graph_conn():
    """Thread-local SQLite connection for code graph DB."""
    conn = getattr(_code_graph_db_pool, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(CODE_GRAPH_DB), exist_ok=True)
        conn = sqlite3.connect(CODE_GRAPH_DB, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _code_graph_db_pool.conn = conn
    return conn


def _code_graph_init_db():
    """Initialize the code graph schema."""
    conn = _code_graph_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS code_nodes (
            qualified_name TEXT PRIMARY KEY,
            file_path TEXT NOT NULL,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            language TEXT,
            line_start INTEGER,
            line_end INTEGER,
            parent_name TEXT,
            params TEXT,
            return_type TEXT,
            modifiers TEXT,
            file_hash TEXT,
            line_count INTEGER
        );
        CREATE TABLE IF NOT EXISTS code_edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            kind TEXT NOT NULL,
            file_path TEXT
        );
        CREATE TABLE IF NOT EXISTS code_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_code_nodes_file ON code_nodes(file_path);
        CREATE INDEX IF NOT EXISTS idx_code_nodes_kind ON code_nodes(kind);
        CREATE INDEX IF NOT EXISTS idx_code_nodes_name ON code_nodes(name);
        CREATE INDEX IF NOT EXISTS idx_code_edges_source ON code_edges(source);
        CREATE INDEX IF NOT EXISTS idx_code_edges_target ON code_edges(target);
    """)
    # Migration: add summary and layer columns
    for col, default in [("summary", "''"), ("layer", "''")]:
        try:
            conn.execute(f"ALTER TABLE code_nodes ADD COLUMN {col} TEXT DEFAULT {default}")
        except Exception:
            pass
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_code_edges_kind ON code_edges(kind);
    """)
    conn.commit()


def _extract_node_name(node):
    """Extract the name from a tree-sitter AST node."""
    # Try field name "name" first
    name_node = node.child_by_field_name("name")
    if name_node:
        return name_node.text.decode("utf-8", errors="replace")
    # Fallback: look for first identifier child
    for child in node.children:
        if child.type == "identifier":
            return child.text.decode("utf-8", errors="replace")
        if child.type == "type_identifier":
            return child.text.decode("utf-8", errors="replace")
    return None


def _extract_call_name(node):
    """Extract the called function/method name from a call expression."""
    fn = node.child_by_field_name("function")
    if fn is None:
        fn = node.child_by_field_name("method")
    if fn is None:
        # Fallback: first child
        if node.children:
            fn = node.children[0]
    if fn is None:
        return None
    # Dotted: a.b.c -> take full text
    text = fn.text.decode("utf-8", errors="replace")
    # For method calls, extract just the method name
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def _extract_import_name(node, language):
    """Extract imported module name from an import node."""
    text = node.text.decode("utf-8", errors="replace")
    if language == "python":
        # import foo / from foo import bar
        if text.startswith("from "):
            parts = text.split()
            if len(parts) >= 2:
                return parts[1]
        elif text.startswith("import "):
            parts = text.split()
            if len(parts) >= 2:
                return parts[1].split(",")[0].strip()
    elif language in ("javascript", "typescript", "tsx"):
        # import ... from "module"
        src = node.child_by_field_name("source")
        if src:
            return src.text.decode("utf-8", errors="replace").strip("'\"")
    elif language == "go":
        # Walk children for interpreted_string_literal
        for child in node.children:
            if child.type == "import_spec_list":
                for spec in child.children:
                    if spec.type == "import_spec":
                        path_node = spec.child_by_field_name("path")
                        if path_node:
                            return path_node.text.decode("utf-8", errors="replace").strip('"')
            elif child.type == "import_spec":
                path_node = child.child_by_field_name("path")
                if path_node:
                    return path_node.text.decode("utf-8", errors="replace").strip('"')
            elif child.type == "interpreted_string_literal":
                return child.text.decode("utf-8", errors="replace").strip('"')
    elif language == "rust":
        # use foo::bar
        if text.startswith("use "):
            return text[4:].rstrip(";").strip()
    elif language == "java":
        # import foo.bar.Baz;
        if text.startswith("import "):
            return text[7:].rstrip(";").strip()
    elif language in ("c", "cpp"):
        # #include <foo> or #include "foo"
        path_node = node.child_by_field_name("path")
        if path_node:
            return path_node.text.decode("utf-8", errors="replace").strip('<>"')
    return text


def _is_test_function(name, file_path):
    """Detect if a function is a test by name or file location."""
    if not name:
        return False
    lower = name.lower()
    if lower.startswith("test_") or (lower.startswith("test") and len(name) > 4 and name[4].isupper()):
        return True
    basename = os.path.basename(file_path).lower()
    if basename.startswith("test_") or basename.endswith("_test.py") or basename.endswith(".test.js") or \
       basename.endswith(".test.ts") or basename.endswith(".test.tsx") or basename.endswith("_test.go") or \
       basename.endswith(".spec.js") or basename.endswith(".spec.ts"):
        return True
    return False


class CodeGraph:
    """AST-based code structure graph using Tree-sitter and SQLite."""

    def __init__(self):
        _code_graph_init_db()
        self._ts_available = None

    def _check_ts(self):
        """Lazy check for tree-sitter availability."""
        if self._ts_available is not None:
            return self._ts_available
        try:
            from tree_sitter_language_pack import get_parser  # noqa: F401
            self._ts_available = True
        except ImportError:
            self._ts_available = False
        return self._ts_available

    def parse_file(self, file_path: str) -> tuple[list[dict], list[dict]]:
        """Parse a single file with tree-sitter. Returns (nodes, edges)."""
        if not self._check_ts():
            return [], []

        ext = os.path.splitext(file_path)[1].lower()
        language = _EXT_TO_LANG.get(ext)
        if not language:
            return [], []

        try:
            from tree_sitter_language_pack import get_parser
        except ImportError:
            return [], []

        try:
            parser = get_parser(language)
        except Exception:
            return [], []

        try:
            with open(file_path, "rb") as f:
                source = f.read()
        except (OSError, IOError):
            return [], []

        try:
            tree = parser.parse(source)
        except Exception:
            return [], []

        nodes = []
        edges = []
        line_count = source.count(b"\n") + 1

        # Compute file hash
        file_hash = hashlib.sha256(source).hexdigest()

        # File node
        file_qn = file_path
        nodes.append({
            "qualified_name": file_qn,
            "file_path": file_path,
            "kind": "file",
            "name": os.path.basename(file_path),
            "language": language,
            "line_start": 1,
            "line_end": line_count,
            "parent_name": None,
            "params": None,
            "return_type": None,
            "modifiers": None,
            "file_hash": file_hash,
            "line_count": line_count,
        })

        class_types = _CLASS_TYPES.get(language, set())
        func_types = _FUNCTION_TYPES.get(language, set())
        import_types = _IMPORT_TYPES.get(language, set())
        call_types = _CALL_TYPES.get(language, set())

        # Walk AST
        def walk(node, parent_class=None):
            ntype = node.type

            if ntype in class_types:
                name = _extract_node_name(node)
                if name:
                    qn = f"{file_path}::{name}"
                    nodes.append({
                        "qualified_name": qn,
                        "file_path": file_path,
                        "kind": "class",
                        "name": name,
                        "language": language,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "parent_name": f"{file_path}::{parent_class}" if parent_class else file_qn,
                        "params": None,
                        "return_type": None,
                        "modifiers": None,
                        "file_hash": None,
                        "line_count": node.end_point[0] - node.start_point[0] + 1,
                    })
                    edges.append({
                        "source": file_qn,
                        "target": qn,
                        "kind": "CONTAINS",
                        "file_path": file_path,
                    })
                    # Check for inheritance
                    superclass = node.child_by_field_name("superclass")
                    if superclass is None:
                        superclass = node.child_by_field_name("argument_list")
                    if superclass is None:
                        superclass = node.child_by_field_name("superclasses")
                    if superclass:
                        sc_text = superclass.text.decode("utf-8", errors="replace").strip("()")
                        if sc_text and sc_text not in ("object", "Object"):
                            for sc in sc_text.split(","):
                                sc = sc.strip()
                                if sc:
                                    edges.append({
                                        "source": qn,
                                        "target": sc,
                                        "kind": "INHERITS",
                                        "file_path": file_path,
                                    })
                    # Recurse with this class as parent
                    for child in node.children:
                        walk(child, parent_class=name)
                    return

            elif ntype in func_types:
                name = _extract_node_name(node)
                if name:
                    if parent_class:
                        qn = f"{file_path}::{parent_class}.{name}"
                    else:
                        qn = f"{file_path}::{name}"

                    is_test = _is_test_function(name, file_path)
                    kind = "test" if is_test else "function"

                    # Extract params
                    params_node = node.child_by_field_name("parameters")
                    params_text = None
                    if params_node:
                        params_text = params_node.text.decode("utf-8", errors="replace")

                    # Extract return type
                    ret_node = node.child_by_field_name("return_type")
                    ret_text = None
                    if ret_node:
                        ret_text = ret_node.text.decode("utf-8", errors="replace")

                    nodes.append({
                        "qualified_name": qn,
                        "file_path": file_path,
                        "kind": kind,
                        "name": name,
                        "language": language,
                        "line_start": node.start_point[0] + 1,
                        "line_end": node.end_point[0] + 1,
                        "parent_name": f"{file_path}::{parent_class}" if parent_class else file_qn,
                        "params": params_text,
                        "return_type": ret_text,
                        "modifiers": None,
                        "file_hash": None,
                        "line_count": node.end_point[0] - node.start_point[0] + 1,
                    })
                    container = f"{file_path}::{parent_class}" if parent_class else file_qn
                    edges.append({
                        "source": container,
                        "target": qn,
                        "kind": "CONTAINS",
                        "file_path": file_path,
                    })

                    # If it's a test, try to link TESTED_BY
                    if is_test:
                        tested_name = name
                        if tested_name.startswith("test_"):
                            tested_name = tested_name[5:]
                        elif tested_name.startswith("Test"):
                            tested_name = tested_name[4:]
                            if tested_name:
                                tested_name = tested_name[0].lower() + tested_name[1:]
                        if tested_name and tested_name != name:
                            edges.append({
                                "source": tested_name,
                                "target": qn,
                                "kind": "TESTED_BY",
                                "file_path": file_path,
                            })

            elif ntype in import_types:
                import_name = _extract_import_name(node, language)
                if import_name:
                    edges.append({
                        "source": file_qn,
                        "target": import_name,
                        "kind": "IMPORTS_FROM",
                        "file_path": file_path,
                    })

            elif ntype in call_types:
                call_name = _extract_call_name(node)
                # Find enclosing function
                enclosing = None
                p = node.parent
                while p:
                    if p.type in func_types:
                        enc_name = _extract_node_name(p)
                        if enc_name:
                            pp = p.parent
                            enc_class = None
                            while pp:
                                if pp.type in class_types:
                                    enc_class = _extract_node_name(pp)
                                    break
                                pp = pp.parent
                            if enc_class:
                                enclosing = f"{file_path}::{enc_class}.{enc_name}"
                            else:
                                enclosing = f"{file_path}::{enc_name}"
                        break
                    p = p.parent
                if call_name and enclosing:
                    edges.append({
                        "source": enclosing,
                        "target": call_name,
                        "kind": "CALLS",
                        "file_path": file_path,
                    })

            # Recurse into children
            for child in node.children:
                walk(child, parent_class=parent_class)

        walk(tree.root_node)
        return nodes, edges

    def build(self, root_dir: str, incremental: bool = True, exclude_dirs: set | None = None) -> dict:
        """Parse all source files in directory and build the graph."""
        if not self._check_ts():
            return {"error": "tree-sitter-language-pack not installed. Run: pip install tree-sitter-language-pack"}

        root_dir = os.path.abspath(root_dir)
        if not os.path.isdir(root_dir):
            return {"error": f"Not a directory: {root_dir}"}

        exclude = exclude_dirs or _DEFAULT_EXCLUDE_DIRS
        conn = _code_graph_conn()

        # Get existing hashes for incremental mode
        existing_hashes = {}
        if incremental:
            try:
                rows = conn.execute(
                    "SELECT file_path, file_hash FROM code_nodes WHERE kind = 'file'"
                ).fetchall()
                existing_hashes = {r[0]: r[1] for r in rows}
            except Exception:
                pass

        stats = {"files_parsed": 0, "files_skipped": 0, "nodes": 0, "edges": 0, "languages": set()}
        all_nodes = []
        all_edges = []

        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Skip excluded directories
            dirnames[:] = [d for d in dirnames if d not in exclude and not d.startswith(".")]

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in _EXT_TO_LANG:
                    continue

                fpath = os.path.join(dirpath, fname)

                # Skip large files (>500KB)
                try:
                    if os.path.getsize(fpath) > 500_000:
                        stats["files_skipped"] += 1
                        continue
                except OSError:
                    continue

                # Check hash for incremental
                if incremental and fpath in existing_hashes:
                    try:
                        with open(fpath, "rb") as f:
                            current_hash = hashlib.sha256(f.read()).hexdigest()
                        if current_hash == existing_hashes[fpath]:
                            stats["files_skipped"] += 1
                            continue
                    except OSError:
                        continue

                nodes, edges = self.parse_file(fpath)
                if nodes:
                    all_nodes.extend(nodes)
                    all_edges.extend(edges)
                    stats["files_parsed"] += 1
                    stats["languages"].add(_EXT_TO_LANG[ext])

        # Bulk insert into SQLite
        if all_nodes or not incremental:
            try:
                if not incremental:
                    conn.execute("DELETE FROM code_nodes")
                    conn.execute("DELETE FROM code_edges")
                else:
                    parsed_files = {n["file_path"] for n in all_nodes if n["kind"] == "file"}
                    for fp in parsed_files:
                        conn.execute("DELETE FROM code_nodes WHERE file_path = ?", (fp,))
                        conn.execute("DELETE FROM code_edges WHERE file_path = ?", (fp,))

                for n in all_nodes:
                    conn.execute(
                        "INSERT OR REPLACE INTO code_nodes "
                        "(qualified_name, file_path, kind, name, language, line_start, line_end, "
                        "parent_name, params, return_type, modifiers, file_hash, line_count) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (n["qualified_name"], n["file_path"], n["kind"], n["name"],
                         n["language"], n["line_start"], n["line_end"], n["parent_name"],
                         n["params"], n["return_type"], n["modifiers"], n["file_hash"],
                         n["line_count"])
                    )

                for e in all_edges:
                    conn.execute(
                        "INSERT INTO code_edges (source, target, kind, file_path) "
                        "VALUES (?, ?, ?, ?)",
                        (e["source"], e["target"], e["kind"], e["file_path"])
                    )

                conn.execute(
                    "INSERT OR REPLACE INTO code_meta (key, value) VALUES (?, ?)",
                    ("last_build", datetime.datetime.now(datetime.timezone.utc).isoformat())
                )
                conn.execute(
                    "INSERT OR REPLACE INTO code_meta (key, value) VALUES (?, ?)",
                    ("root_dir", root_dir)
                )
                conn.commit()
            except Exception as e:
                try:
                    conn.rollback()
                except Exception:
                    pass
                return {"error": f"Database error: {e}"}

        stats["nodes"] = len(all_nodes)
        stats["edges"] = len(all_edges)
        stats["languages"] = sorted(stats["languages"])

        try:
            total_nodes = conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0]
            total_edges = conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
            stats["total_nodes"] = total_nodes
            stats["total_edges"] = total_edges
        except Exception:
            pass

        return stats

    def query(self, query_type: str, target: str, limit: int = 20) -> list[dict]:
        """Run predefined structural queries."""
        conn = _code_graph_conn()
        results = []

        try:
            if query_type == "callers_of":
                rows = conn.execute(
                    "SELECT e.source, e.file_path, n.kind, n.line_start, n.line_end "
                    "FROM code_edges e LEFT JOIN code_nodes n ON e.source = n.qualified_name "
                    "WHERE e.kind = 'CALLS' AND (e.target = ? OR e.target LIKE ?) LIMIT ?",
                    (target, f"%::{target}", limit)
                ).fetchall()
                for r in rows:
                    results.append({"caller": r[0], "file": r[1], "kind": r[2], "line_start": r[3], "line_end": r[4]})

            elif query_type == "callees_of":
                rows = conn.execute(
                    "SELECT e.target, e.file_path "
                    "FROM code_edges e "
                    "WHERE e.kind = 'CALLS' AND (e.source = ? OR e.source LIKE ?) LIMIT ?",
                    (target, f"%::{target}", limit)
                ).fetchall()
                for r in rows:
                    results.append({"callee": r[0], "file": r[1]})

            elif query_type == "imports_of":
                rows = conn.execute(
                    "SELECT e.target, e.file_path "
                    "FROM code_edges e "
                    "WHERE e.kind = 'IMPORTS_FROM' AND (e.source = ? OR e.source LIKE ?) LIMIT ?",
                    (target, f"%{target}%", limit)
                ).fetchall()
                for r in rows:
                    results.append({"imports": r[0], "file": r[1]})

            elif query_type == "importers_of":
                rows = conn.execute(
                    "SELECT e.source, e.file_path "
                    "FROM code_edges e "
                    "WHERE e.kind = 'IMPORTS_FROM' AND (e.target = ? OR e.target LIKE ?) LIMIT ?",
                    (target, f"%{target}%", limit)
                ).fetchall()
                for r in rows:
                    results.append({"importer": r[0], "file": r[1]})

            elif query_type == "tests_for":
                rows = conn.execute(
                    "SELECT e.target, e.file_path, n.line_start, n.line_end "
                    "FROM code_edges e LEFT JOIN code_nodes n ON e.target = n.qualified_name "
                    "WHERE e.kind = 'TESTED_BY' AND (e.source = ? OR e.source LIKE ?) LIMIT ?",
                    (target, f"%::{target}", limit)
                ).fetchall()
                for r in rows:
                    results.append({"test": r[0], "file": r[1], "line_start": r[2], "line_end": r[3]})

            elif query_type == "inheritors_of":
                rows = conn.execute(
                    "SELECT e.source, e.file_path, n.line_start, n.line_end "
                    "FROM code_edges e LEFT JOIN code_nodes n ON e.source = n.qualified_name "
                    "WHERE e.kind = 'INHERITS' AND (e.target = ? OR e.target LIKE ?) LIMIT ?",
                    (target, f"%::{target}", limit)
                ).fetchall()
                for r in rows:
                    results.append({"inheritor": r[0], "file": r[1], "line_start": r[2], "line_end": r[3]})

            elif query_type == "children_of":
                rows = conn.execute(
                    "SELECT e.target, n.kind, n.name, n.line_start, n.line_end, n.params "
                    "FROM code_edges e LEFT JOIN code_nodes n ON e.target = n.qualified_name "
                    "WHERE e.kind = 'CONTAINS' AND (e.source = ? OR e.source LIKE ?) LIMIT ?",
                    (target, f"%::{target}", limit)
                ).fetchall()
                for r in rows:
                    results.append({"child": r[0], "kind": r[1], "name": r[2], "line_start": r[3], "line_end": r[4], "params": r[5]})

            elif query_type == "file_summary":
                rows = conn.execute(
                    "SELECT qualified_name, kind, name, line_start, line_end, params, return_type "
                    "FROM code_nodes WHERE file_path = ? OR file_path LIKE ? ORDER BY line_start LIMIT ?",
                    (target, f"%{target}", limit)
                ).fetchall()
                for r in rows:
                    results.append({
                        "qualified_name": r[0], "kind": r[1], "name": r[2],
                        "line_start": r[3], "line_end": r[4], "params": r[5], "return_type": r[6],
                    })

        except Exception as e:
            return [{"error": str(e)}]

        return results

    def impact_analysis(self, files: list[str], depth: int = 2) -> dict:
        """BFS blast-radius analysis using networkx."""
        try:
            import networkx as nx
        except ImportError:
            return {"error": "networkx not installed. Run: pip install networkx"}

        conn = _code_graph_conn()

        G = nx.DiGraph()
        try:
            edges = conn.execute("SELECT source, target, kind FROM code_edges").fetchall()
            for src, tgt, kind in edges:
                G.add_edge(src, tgt, kind=kind)
                G.add_edge(tgt, src, kind=f"REV_{kind}")
        except Exception as e:
            return {"error": f"Database error: {e}"}

        changed_nodes = set()
        for fp in files:
            fp = os.path.abspath(fp)
            try:
                rows = conn.execute(
                    "SELECT qualified_name FROM code_nodes WHERE file_path = ?", (fp,)
                ).fetchall()
                for r in rows:
                    changed_nodes.add(r[0])
            except Exception:
                pass

        if not changed_nodes:
            return {
                "changed_nodes": [],
                "impacted_nodes": [],
                "impacted_files": [],
                "warnings": ["No nodes found for the specified files. Run code_graph_build first."],
            }

        impacted = set()
        frontier = set(changed_nodes)
        visited = set(changed_nodes)
        for _ in range(depth):
            next_frontier = set()
            for node in frontier:
                if node in G:
                    for neighbor in G.neighbors(node):
                        if neighbor not in visited:
                            visited.add(neighbor)
                            next_frontier.add(neighbor)
                            impacted.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break

        impacted_files = set()
        for qn in impacted:
            try:
                row = conn.execute(
                    "SELECT file_path FROM code_nodes WHERE qualified_name = ?", (qn,)
                ).fetchone()
                if row:
                    impacted_files.add(row[0])
            except Exception:
                pass

        warnings = []
        for qn in changed_nodes:
            try:
                test_count = conn.execute(
                    "SELECT COUNT(*) FROM code_edges WHERE kind = 'TESTED_BY' AND source = ?", (qn,)
                ).fetchone()[0]
                if test_count == 0:
                    row = conn.execute(
                        "SELECT kind, name FROM code_nodes WHERE qualified_name = ?", (qn,)
                    ).fetchone()
                    if row and row[0] in ("function", "class"):
                        warnings.append(f"No tests found for {row[1]} ({qn})")
            except Exception:
                pass

        return {
            "changed_nodes": sorted(changed_nodes),
            "impacted_nodes": sorted(impacted),
            "impacted_files": sorted(impacted_files),
            "warnings": warnings,
        }

    def get_stats(self) -> dict:
        """Return node/edge counts and language distribution."""
        conn = _code_graph_conn()
        try:
            total_nodes = conn.execute("SELECT COUNT(*) FROM code_nodes").fetchone()[0]
            total_edges = conn.execute("SELECT COUNT(*) FROM code_edges").fetchone()[0]
            kind_dist = conn.execute(
                "SELECT kind, COUNT(*) FROM code_nodes GROUP BY kind ORDER BY COUNT(*) DESC"
            ).fetchall()
            lang_dist = conn.execute(
                "SELECT language, COUNT(*) FROM code_nodes WHERE language IS NOT NULL GROUP BY language ORDER BY COUNT(*) DESC"
            ).fetchall()
            edge_dist = conn.execute(
                "SELECT kind, COUNT(*) FROM code_edges GROUP BY kind ORDER BY COUNT(*) DESC"
            ).fetchall()
            last_build = conn.execute(
                "SELECT value FROM code_meta WHERE key = 'last_build'"
            ).fetchone()
            root_dir = conn.execute(
                "SELECT value FROM code_meta WHERE key = 'root_dir'"
            ).fetchone()
            return {
                "total_nodes": total_nodes,
                "total_edges": total_edges,
                "node_kinds": {k: c for k, c in kind_dist},
                "languages": {k: c for k, c in lang_dist},
                "edge_kinds": {k: c for k, c in edge_dist},
                "last_build": last_build[0] if last_build else None,
                "root_dir": root_dir[0] if root_dir else None,
            }
        except Exception as e:
            return {"error": str(e)}

    def update_file(self, file_path: str):
        """Incrementally re-parse a single file and update the graph."""
        file_path = os.path.abspath(file_path)
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in _EXT_TO_LANG:
            return

        conn = _code_graph_conn()
        nodes, edges = self.parse_file(file_path)
        if not nodes:
            return

        try:
            conn.execute("DELETE FROM code_nodes WHERE file_path = ?", (file_path,))
            conn.execute("DELETE FROM code_edges WHERE file_path = ?", (file_path,))
            for n in nodes:
                conn.execute(
                    "INSERT OR REPLACE INTO code_nodes "
                    "(qualified_name, file_path, kind, name, language, line_start, line_end, "
                    "parent_name, params, return_type, modifiers, file_hash, line_count) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (n["qualified_name"], n["file_path"], n["kind"], n["name"],
                     n["language"], n["line_start"], n["line_end"], n["parent_name"],
                     n["params"], n["return_type"], n["modifiers"], n["file_hash"],
                     n["line_count"])
                )
            for e in edges:
                conn.execute(
                    "INSERT INTO code_edges (source, target, kind, file_path) "
                    "VALUES (?, ?, ?, ?)",
                    (e["source"], e["target"], e["kind"], e["file_path"])
                )
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass


    # --- Architecture Layer Classification ---

    _LAYER_PATTERNS = {
        "api": ["route", "router", "handler", "endpoint", "controller", "view", "api", "rest", "graphql", "grpc"],
        "service": ["service", "manager", "engine", "processor", "worker", "scheduler", "pipeline"],
        "data": ["model", "schema", "migration", "orm", "database", "db", "query", "repository", "dao", "store"],
        "ui": ["component", "page", "layout", "widget", "template", "style", "css", "html", "view", "screen", "form"],
        "util": ["util", "helper", "common", "shared", "lib", "config", "constant", "middleware", "decorator", "mixin"],
        "test": ["test", "spec", "fixture", "mock", "stub", "fake"],
    }

    def classify_layers(self) -> dict:
        """Classify all nodes into architecture layers based on file paths and names."""
        conn = _code_graph_conn()
        rows = conn.execute("SELECT qualified_name, file_path, name, kind FROM code_nodes").fetchall()
        classified = 0
        for qn, fp, name, kind in rows:
            layer = self._detect_layer(fp, name, kind)
            if layer:
                conn.execute("UPDATE code_nodes SET layer = ? WHERE qualified_name = ?", (layer, qn))
                classified += 1
        conn.commit()
        # Get distribution
        dist = conn.execute(
            "SELECT layer, COUNT(*) FROM code_nodes WHERE layer != '' GROUP BY layer ORDER BY COUNT(*) DESC"
        ).fetchall()
        return {"classified": classified, "layers": {k: c for k, c in dist}}

    def _detect_layer(self, file_path: str, name: str, kind: str) -> str:
        """Detect architecture layer from file path and node name."""
        fp_lower = file_path.lower().replace("\\", "/")
        name_lower = name.lower()
        combined = fp_lower + "/" + name_lower

        if kind == "test":
            return "test"

        for layer, patterns in self._LAYER_PATTERNS.items():
            for p in patterns:
                if p in combined:
                    return layer
        return ""

    # --- LLM-Generated Summaries ---

    def generate_summaries(self, batch_size: int = 20) -> dict:
        """Generate plain-English summaries for nodes that don't have one.
        Uses a cheap LLM (Haiku) to describe functions/classes from their signature + context."""
        conn = _code_graph_conn()
        # Get nodes without summaries (classes and functions only)
        rows = conn.execute(
            "SELECT qualified_name, file_path, kind, name, params, return_type, line_start, line_end, language "
            "FROM code_nodes WHERE (summary IS NULL OR summary = '') AND kind IN ('class', 'function') "
            "ORDER BY file_path, line_start LIMIT ?",
            (batch_size * 5,)  # over-fetch to batch by file
        ).fetchall()

        if not rows:
            return {"summarized": 0, "message": "All nodes already have summaries"}

        # Resolve summary model — server default; admin picks the install's
        # default and we use it (no haiku/cheapest heuristics).
        model = _background_model_default()
        if not model:
            return {"error": "No model available for summary generation"}

        # Group by file for efficient source reading
        file_groups = {}
        for r in rows:
            fp = r[1]
            if fp not in file_groups:
                file_groups[fp] = []
            file_groups[fp].append(r)

        summarized = 0
        for fp, file_nodes in list(file_groups.items())[:batch_size]:
            # Read source file
            try:
                with open(fp, "r", errors="replace") as f:
                    lines = f.readlines()
            except (OSError, IOError):
                continue

            # Build batch prompt
            node_snippets = []
            for qn, _, kind, name, params, ret_type, ls, le, lang in file_nodes:
                snippet = "".join(lines[max(0, ls-1):min(len(lines), le)])[:500]
                sig = f"{kind} {name}"
                if params:
                    sig += f"({params})"
                if ret_type:
                    sig += f" -> {ret_type}"
                node_snippets.append({"qn": qn, "sig": sig, "snippet": snippet})

            if not node_snippets:
                continue

            prompt_parts = [f"**{s['sig']}**\n```\n{s['snippet']}\n```" for s in node_snippets]
            prompt = (
                f"For each function/class below from `{os.path.basename(fp)}`, write a ONE-LINE summary "
                f"(max 80 chars) describing what it does. Output as numbered list matching the order.\n\n"
                + "\n\n".join(f"{i+1}. {p}" for i, p in enumerate(prompt_parts))
            )

            try:
                from handlers import sidecar_proxy as _sidecar_proxy
                _res = _sidecar_proxy.background_call(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                    system_prompt="Output only numbered one-line summaries. No markdown, no explanations.",
                    cost_purpose="code_graph_summary",
                    max_tokens=2000,
                )
                result = _res.get("reply") or ""
                if result and not _res.get("error"):
                    # Parse numbered lines
                    summary_lines = []
                    for line in result.strip().split("\n"):
                        line = line.strip()
                        if line and line[0].isdigit():
                            # Strip number prefix
                            parts = line.split(".", 1)
                            if len(parts) == 2:
                                summary_lines.append(parts[1].strip())
                            else:
                                summary_lines.append(line)

                    for i, s in enumerate(node_snippets):
                        summary = summary_lines[i] if i < len(summary_lines) else ""
                        if summary:
                            conn.execute("UPDATE code_nodes SET summary = ? WHERE qualified_name = ?",
                                        (summary[:200], s["qn"]))
                            summarized += 1
            except Exception:
                continue

        conn.commit()
        return {"summarized": summarized, "total_pending": len(rows) - summarized}

    # --- Guided Tour Generation ---

    def generate_tour(self, root_dir: str | None = None) -> str:
        """Generate a dependency-ordered guided tour of the codebase."""
        conn = _code_graph_conn()
        if not root_dir:
            row = conn.execute("SELECT value FROM code_meta WHERE key = 'root_dir'").fetchone()
            root_dir = row[0] if row else "."

        # Get all file nodes with summaries and layers
        files = conn.execute(
            "SELECT qualified_name, name, language, line_count, layer, summary "
            "FROM code_nodes WHERE kind = 'file' ORDER BY qualified_name"
        ).fetchall()

        if not files:
            return "No files in the code graph. Run code_graph_build first."

        # Build dependency order: files with fewest imports first
        file_imports = {}
        for f in files:
            imports = conn.execute(
                "SELECT COUNT(*) FROM code_edges WHERE source = ? AND kind = 'IMPORTS_FROM'",
                (f[0],)
            ).fetchone()[0]
            importers = conn.execute(
                "SELECT COUNT(*) FROM code_edges WHERE target LIKE ? AND kind = 'IMPORTS_FROM'",
                (f"%%{f[1]}%%",)
            ).fetchone()[0]
            file_imports[f[0]] = {"imports": imports, "importers": importers}

        # Sort: foundation files first (many importers, few imports), then leaves
        sorted_files = sorted(files, key=lambda f: (
            file_imports.get(f[0], {}).get("imports", 0) - file_imports.get(f[0], {}).get("importers", 0),
            f[0]
        ))

        # Group by layer
        layer_groups = {}
        for f in sorted_files:
            layer = f[4] or "other"
            if layer not in layer_groups:
                layer_groups[layer] = []
            layer_groups[layer].append(f)

        # Build tour markdown
        tour = f"# Codebase Tour: {os.path.basename(root_dir)}\n\n"
        tour += f"**{len(files)} files** across {len(set(f[2] for f in files if f[2]))} language(s)\n\n"

        # Architecture overview
        if layer_groups:
            tour += "## Architecture Layers\n\n"
            layer_order = ["api", "service", "data", "ui", "util", "test", "other"]
            layer_emoji = {"api": "🌐", "service": "⚙️", "data": "💾", "ui": "🖥️", "util": "🔧", "test": "🧪", "other": "📁"}
            for layer in layer_order:
                if layer in layer_groups:
                    files_in_layer = layer_groups[layer]
                    tour += f"### {layer_emoji.get(layer, '📁')} {layer.upper()} ({len(files_in_layer)} files)\n\n"
                    for f in files_in_layer[:10]:
                        qn, name, lang, lc, _, summary = f
                        rel = os.path.relpath(qn, root_dir) if root_dir else qn
                        imp_count = file_imports.get(qn, {}).get("importers", 0)
                        desc = f" — {summary}" if summary else ""
                        tour += f"- `{rel}` ({lang}, {lc} lines, {imp_count} dependents){desc}\n"
                    if len(files_in_layer) > 10:
                        tour += f"- ... and {len(files_in_layer) - 10} more\n"
                    tour += "\n"

        # Entry points
        tour += "## Suggested Reading Order\n\n"
        tour += "Start with the foundation (most imported) files, then work outward:\n\n"
        for i, f in enumerate(sorted_files[:15], 1):
            qn, name, lang, lc, layer, summary = f
            rel = os.path.relpath(qn, root_dir) if root_dir else qn
            imp = file_imports.get(qn, {})
            desc = f" — {summary}" if summary else ""
            tour += f"{i}. `{rel}` ({imp.get('importers', 0)} dependents, {imp.get('imports', 0)} imports){desc}\n"

        # Get key classes
        key_classes = conn.execute(
            "SELECT n.qualified_name, n.name, n.file_path, n.summary, COUNT(e.id) as edge_count "
            "FROM code_nodes n LEFT JOIN code_edges e ON n.qualified_name = e.source OR n.qualified_name = e.target "
            "WHERE n.kind = 'class' GROUP BY n.qualified_name ORDER BY edge_count DESC LIMIT 10"
        ).fetchall()
        if key_classes:
            tour += "\n## Key Classes\n\n"
            for qn, name, fp, summary, edges in key_classes:
                rel = os.path.relpath(fp, root_dir) if root_dir else fp
                desc = f" — {summary}" if summary else ""
                tour += f"- **{name}** in `{rel}` ({edges} connections){desc}\n"

        return tour


_code_graph: CodeGraph | None = None


def _get_code_graph() -> CodeGraph:
    """Get or create the global CodeGraph instance."""
    global _code_graph
    if _code_graph is None:
        _code_graph = CodeGraph()
    return _code_graph


def _maybe_update_code_graph(path: str):
    """Update code graph if the file is a supported source file."""
    ext = os.path.splitext(path)[1].lower()
    if ext not in _EXT_TO_LANG:
        return
    try:
        cg = _get_code_graph()
        if cg._check_ts():
            cg.update_file(path)
    except Exception:
        pass


def tool_code_graph_build(args: dict) -> str:
    """Build or rebuild the code structure graph."""
    path = args.get("path", ".")
    incremental = args.get("incremental", True)
    cg = _get_code_graph()
    stats = cg.build(path, incremental=incremental)
    if "error" in stats:
        return _err(stats["error"])
    return _ok(stats)


def tool_code_graph_query(args: dict) -> str:
    """Query the code structure graph."""
    query_type = args.get("query_type", "")
    target = args.get("target", "")
    limit = args.get("limit", 20)
    if not query_type:
        return _err("Missing query_type")
    if not target:
        return _err("Missing target")
    cg = _get_code_graph()
    results = cg.query(query_type, target, limit)
    return _ok({"query_type": query_type, "target": target, "results": results, "count": len(results)})


def tool_code_graph_impact(args: dict) -> str:
    """Blast-radius impact analysis."""
    files = args.get("files", [])
    depth = args.get("depth", 2)
    if not files:
        return _err("Missing files list")
    cg = _get_code_graph()
    result = cg.impact_analysis(files, depth=depth)
    if "error" in result:
        return _err(result["error"])
    return _ok(result)


def tool_code_graph_enhance(args: dict) -> str:
    """Enhance the code graph with LLM summaries, architecture layers, and guided tour."""
    action = args.get("action", "all")
    cg = _get_code_graph()

    result = {}
    if action in ("all", "layers"):
        result["layers"] = cg.classify_layers()
    if action in ("all", "summaries"):
        batch_size = args.get("batch_size", 20)
        result["summaries"] = cg.generate_summaries(batch_size=batch_size)
    if action in ("all", "tour"):
        root_dir = args.get("root_dir")
        result["tour"] = cg.generate_tour(root_dir=root_dir)
    return _ok(result)

