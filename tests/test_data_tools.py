"""Tests for data_query + db_query (engine/tools/data_tools.py) — Quant-
Workbench D1 + D2.

The read-only contract is the point of this suite (plan: the three rejections
explicit — INSERT, COPY TO, multi-statement): layer (a) is the shared
SELECT/WITH prefix check, layer (c) is the DuckDB engine lockdown
(allowed_paths + enable_external_access=false + lock_configuration), which
must also block file reads OUTSIDE the passed inputs from within a SELECT.

db_query (D2): layer 2 must be provable INDEPENDENTLY of layer 1 — a direct
INSERT on the tool's own connection has to die in the SESSION
(ReadOnlySqlTransaction), not in the prefix check. The db suite needs the
local test postgres (dbname=braintest) and skips cleanly without it.

Run: python3 -m unittest tests.test_data_tools -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import duckdb  # noqa: F401
    _HAVE_DUCKDB = True
except ImportError:
    _HAVE_DUCKDB = False

import brain  # noqa: E402,F401  (loads TOOL_DISPATCH etc.)
from engine.context import request_context  # noqa: E402
from engine.tools import data_tools  # noqa: E402


class _FakeAgent:
    agent_id = "main"


@unittest.skipUnless(_HAVE_DUCKDB, "duckdb not installed")
class _DataFixture(unittest.TestCase):
    """trades.parquet (1000 rows) + branches.csv + store.duckdb, queried via
    a request context with a unique session id (same pattern as
    test_xlsx_tools._ToolFixture)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="datatools_test_")
        self.pq = os.path.join(self._tmp, "trades.parquet")
        self.cs = os.path.join(self._tmp, "branches.csv")
        self.db = os.path.join(self._tmp, "store.duckdb")
        self.secret = os.path.join(self._tmp, "secret.csv")
        seed = duckdb.connect(":memory:")
        seed.execute(f"""
            COPY (SELECT (i % 3) + 1 AS branch_id,
                         (i * 37 % 1000) / 10.0 AS betrag
                  FROM range(1000) t(i))
            TO '{self.pq}' (FORMAT PARQUET)""")
        seed.close()
        with open(self.cs, "w", encoding="utf-8") as f:
            f.write("branch_id;name\n1;Nord\n2;Sued\n3;West\n")
        with open(self.secret, "w", encoding="utf-8") as f:
            f.write("a\ndo-not-read\n")
        store = duckdb.connect(self.db)
        store.execute(
            "CREATE TABLE limits AS SELECT i AS branch_id, i * 100.0 AS max_exposure "
            "FROM range(1, 4) t(i)")
        store.close()
        self._prev_cwd = os.getcwd()
        os.chdir(self._tmp)
        self._sid = "datatools-test-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent()))

    def tearDown(self):
        os.chdir(self._prev_cwd)
        try:
            import shutil
            from engine.tools.file_tools import _resolve_artifact_dir
            with request_context(current_session_id=self._sid,
                                 current_agent=_FakeAgent()):
                art_dir, _ = _resolve_artifact_dir()
            if art_dir and self._sid in art_dir and os.path.isdir(art_dir):
                shutil.rmtree(art_dir)
        except Exception:
            pass

    def _q(self, **args):
        return json.loads(data_tools.tool_data_query(args))


class TestDataQuery(_DataFixture):
    def test_parquet_aggregate(self):
        out = self._q(path=self.pq,
                      sql=("SELECT branch_id, COUNT(*) AS n FROM trades "
                           "GROUP BY branch_id ORDER BY branch_id"))
        self.assertEqual(out["row_count"], 3)
        self.assertIn("| 1 | 334 |", out["result"])
        self.assertTrue(any(v.startswith("trades (1,000 rows)")
                            for v in out["views"]), out["views"])

    def test_parquet_csv_cross_join(self):
        out = self._q(paths=[self.pq, self.cs],
                      sql=("SELECT b.name, COUNT(*) AS n FROM trades t "
                           "JOIN branches b USING(branch_id) "
                           "GROUP BY b.name ORDER BY b.name"))
        self.assertEqual(out["row_count"], 3)
        self.assertIn("| Nord | 334 |", out["result"])

    def test_duckdb_file_tables(self):
        out = self._q(path=self.db,
                      sql="SELECT COUNT(*) AS n, MAX(max_exposure) FROM limits")
        self.assertEqual(out["row_count"], 1)
        self.assertIn("| 3 | 300.0 |", out["result"])

    def test_display_cap_50_rows(self):
        out = self._q(path=self.pq, sql="SELECT * FROM trades")
        self.assertEqual(out["row_count"], 1000)
        self.assertIn("showing first 50", out["result"])
        # 50 data rows + header + separator
        self.assertLessEqual(out["result"].count("\n| "), 52)

    # --- the read-only contract ------------------------------------------

    def test_rejects_insert(self):
        out = self._q(path=self.pq, sql="INSERT INTO trades VALUES (1, 1.0)")
        self.assertIn("error", out)
        self.assertIn("SELECT", out["error"])

    def test_rejects_copy_to(self):
        out = self._q(path=self.pq,
                      sql=f"COPY (SELECT 1) TO '{self._tmp}/evil.csv'")
        self.assertIn("error", out)
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "evil.csv")))

    def test_rejects_multi_statement(self):
        out = self._q(path=self.pq,
                      sql="SELECT 1; COPY (SELECT 1) TO 'evil.csv'")
        self.assertIn("error", out)
        self.assertIn("ONE statement", out["error"])
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "evil.csv")))

    def test_engine_blocks_reading_other_files(self):
        # Passes the prefix check (it IS a SELECT) — must die in the engine
        # lockdown (allowed_paths), not read the file.
        out = self._q(path=self.pq,
                      sql=f"SELECT * FROM read_csv_auto('{self.secret}')")
        self.assertIn("error", out)
        self.assertNotIn("do-not-read", json.dumps(out))

    def test_engine_blocks_reenabling_external_access(self):
        out = self._q(path=self.pq, sql="SET enable_external_access = true")
        self.assertIn("error", out)

    # --- self-correction + artifacts ---------------------------------------

    def test_sql_error_echoes_schema(self):
        out = self._q(path=self.pq, sql="SELECT nope FROM trades")
        self.assertIn("error", out)
        self.assertIn("Views for data_query:", out["error"])
        self.assertIn("trades(", out["error"])
        self.assertIn("branch_id", out["error"])

    def test_out_writes_full_csv(self):
        out = self._q(path=self.pq,
                      sql="SELECT * FROM trades ORDER BY betrag",
                      out="full.csv")
        self.assertEqual(out["saved"]["rows"], 1000)
        with open(out["saved"]["path"], encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), 1001)  # header + 1000 rows

    def test_result_row_cap(self):
        big = os.path.join(self._tmp, "big.parquet")
        seed = duckdb.connect(":memory:")
        seed.execute(f"COPY (SELECT i FROM range({data_tools.DATA_MAX_RESULT_ROWS + 1}) t(i)) "
                     f"TO '{big}' (FORMAT PARQUET)")
        seed.close()
        out = self._q(path=big, sql="SELECT * FROM big")
        self.assertIn("error", out)
        self.assertIn("aggregate or filter", out["error"])

    def test_unsupported_ext_points_to_xlsx_query(self):
        xlsx = os.path.join(self._tmp, "wb.xlsx")
        with open(xlsx, "w") as f:
            f.write("x")
        out = self._q(path=xlsx, sql="SELECT 1")
        self.assertIn("error", out)
        self.assertIn("xlsx_query", out["error"])

    def test_missing_file(self):
        out = self._q(path="nope.parquet", sql="SELECT 1")
        self.assertIn("error", out)
        self.assertIn("not found", out["error"])


# ---------------------------------------------------------------------------
# db_query (D2)
# ---------------------------------------------------------------------------

_PG_DSN = "postgresql://brain_ro:brain_ro_test@localhost:5432/braintest"
_PG_OWNER_DSN = "dbname=braintest host=localhost"


def _pg_available() -> bool:
    try:
        import psycopg2
        conn = psycopg2.connect(_PG_DSN, connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


_HAVE_PG = _pg_available()

from unittest import mock  # noqa: E402

_TEST_SOURCES = [
    {"name": "braintest", "type": "postgres", "dsn": _PG_DSN,
     "options": {"statement_timeout_ms": 60000, "connect_timeout": 5}},
    # Layer-2 proof source: OWNER credentials (may write by grant) — the
    # session read-only must still block writes.
    {"name": "braintest_owner", "type": "postgres", "dsn": _PG_OWNER_DSN},
    {"name": "dead_db", "type": "postgres",
     "dsn": "postgresql://x:x@localhost:59999/nope",
     "options": {"connect_timeout": 2}},
    {"name": "exotic", "type": "snowflake", "dsn": "irrelevant"},
]


@unittest.skipUnless(_HAVE_PG, "local test postgres (braintest) not available")
class TestDbQuery(unittest.TestCase):
    def setUp(self):
        self._sid = "datatools-test-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent()))
        self.enterContext(mock.patch.object(
            data_tools, "_data_sources", return_value=_TEST_SOURCES))

    def tearDown(self):
        try:
            import shutil
            from engine.tools.file_tools import _resolve_artifact_dir
            with request_context(current_session_id=self._sid,
                                 current_agent=_FakeAgent()):
                art_dir, _ = _resolve_artifact_dir()
            if art_dir and self._sid in art_dir and os.path.isdir(art_dir):
                shutil.rmtree(art_dir)
        except Exception:
            pass

    def _q(self, **args):
        return json.loads(data_tools.tool_db_query(args))

    def test_select_works(self):
        out = self._q(source="braintest",
                      sql="SELECT COUNT(*) AS n, SUM(stueck) AS s FROM positionen")
        self.assertEqual(out["row_count"], 1)
        self.assertIn("| 50000 | 12525000 |", out["result"])
        self.assertIn("read-only", out["read_only"])

    def test_rejects_insert_layer1(self):
        out = self._q(source="braintest",
                      sql="INSERT INTO positionen (filiale_id, isin, stueck, kurs) "
                          "VALUES (1,'X',1,1)")
        self.assertIn("error", out)
        self.assertIn("SELECT", out["error"])

    def test_layer2_blocks_insert_on_the_session(self):
        """Layer 2 proof, independent of layer 1: a direct INSERT on the
        tool's OWN connection (owner creds — grants would allow the write)
        must die in the read-only SESSION."""
        src = data_tools._resolve_db_source("braintest_owner")
        conn, cur = data_tools._connect_readonly(src)
        try:
            plain = conn.cursor()
            with self.assertRaises(Exception) as ctx:
                plain.execute("INSERT INTO positionen "
                              "(filiale_id, isin, stueck, kurs) VALUES (1,'X',1,1)")
            self.assertIn("read-only", str(ctx.exception))
        finally:
            conn.close()

    def test_rejects_multi_statement(self):
        out = self._q(source="braintest",
                      sql="SELECT 1; DELETE FROM positionen")
        self.assertIn("error", out)
        self.assertIn("ONE statement", out["error"])

    def test_unknown_source_lists_available(self):
        out = self._q(source="nope", sql="SELECT 1")
        self.assertIn("error", out)
        self.assertIn("braintest", out["error"])

    def test_unwired_type_fails_loud(self):
        out = self._q(source="exotic", sql="SELECT 1")
        self.assertIn("error", out)
        self.assertIn("snowflake", out["error"])
        self.assertIn("postgres", out["error"])

    def test_dead_connection_clean_error(self):
        out = self._q(source="dead_db", sql="SELECT 1")
        self.assertIn("error", out)
        self.assertIn("connection failed", out["error"])

    def test_sql_error_hints_information_schema(self):
        out = self._q(source="braintest", sql="SELECT nope FROM positionen")
        self.assertIn("error", out)
        self.assertIn("information_schema", out["error"])

    def test_out_writes_full_csv(self):
        out = self._q(source="braintest",
                      sql="SELECT * FROM positionen WHERE filiale_id = 1 "
                          "ORDER BY id",
                      out="fil1.csv")
        self.assertEqual(out["saved"]["rows"], 2000)
        with open(out["saved"]["path"], encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), 2001)


if __name__ == "__main__":
    unittest.main()
