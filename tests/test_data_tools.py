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
    # rw source (E5): owner creds + access_mode rw — writes pass layer 1
    # and the session is NOT opened read-only.
    {"name": "braintest_rw", "type": "postgres", "dsn": _PG_OWNER_DSN,
     "access_mode": "rw"},
    {"name": "dead_db", "type": "postgres",
     "dsn": "postgresql://x:x@localhost:59999/nope",
     "options": {"connect_timeout": 2}},
    {"name": "exotic", "type": "snowflake", "dsn": "irrelevant"},
]


@unittest.skipUnless(_HAVE_PG, "local test postgres (braintest) not available")
class TestDbQuery(unittest.TestCase):
    def setUp(self):
        self._sid = "datatools-test-" + uuid.uuid4().hex[:8]
        # __system__ passes the v9.363.0 access policy — this suite tests
        # query mechanics, not the policy (TestDbQueryAccessPolicy does).
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent(),
            current_user_id="__system__"))
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


class TestDbQueryAccessPolicy(unittest.TestCase):
    """v9.363.0 db_query access policy: `enabled` is a master switch for
    EVERYONE (admins included); grants are additive (role OR team OR user);
    admins bypass the grant axes; a MISSING config block means admins only."""

    def _check(self, pol, *, user, member_of=(), uid="u1"):
        cfg = {"data_sources_access": pol} if pol is not None else {}
        with mock.patch.object(brain, "_server_config", return_value=cfg), \
             mock.patch("server_lib.auth.AuthDB.get_user",
                        lambda _uid: user), \
             mock.patch("server_lib.auth.AuthDB.get_user_teams",
                        lambda _uid: [{"id": t} for t in member_of]):
            return data_tools.data_access_allowed(uid)

    def test_missing_block_means_admins_only(self):
        self.assertTrue(self._check(None, user={"role": "admin"})[0])
        ok, why = self._check(None, user={"role": "user"})
        self.assertFalse(ok)
        self.assertIn("grant", why)

    def test_master_switch_off_blocks_even_admins(self):
        ok, why = self._check({"enabled": False}, user={"role": "admin"})
        self.assertFalse(ok)
        self.assertIn("globally", why)

    def test_role_grant(self):
        pol = {"enabled": True, "roles": ["user"]}
        self.assertTrue(self._check(pol, user={"role": "user"})[0])
        self.assertFalse(self._check(pol, user={"role": "poweruser"})[0])

    def test_user_grant(self):
        pol = {"enabled": True, "roles": [], "users": ["u1"]}
        self.assertTrue(self._check(pol, user={"role": "user"}, uid="u1")[0])
        self.assertFalse(self._check(pol, user={"role": "user"}, uid="u2")[0])

    def test_team_grant(self):
        pol = {"enabled": True, "roles": [], "teams": ["t-fin"]}
        self.assertTrue(self._check(pol, user={"role": "user"},
                                    member_of=("t-fin", "t-x"))[0])
        self.assertFalse(self._check(pol, user={"role": "user"},
                                     member_of=("t-x",))[0])

    def test_system_and_empty_user(self):
        pol = {"enabled": True, "roles": []}
        self.assertTrue(self._check(pol, user=None, uid="__system__")[0])
        self.assertFalse(self._check(pol, user=None, uid="")[0])

    def test_tool_guard_denies_before_source_resolution(self):
        """The guard sits at the TOP of tool_db_query — a denied user gets the
        access error, never a connection attempt (no source config needed)."""
        with request_context(current_session_id="ds-pol-test",
                             current_agent=_FakeAgent(),
                             current_user_id="u1"), \
             mock.patch.object(brain, "_server_config",
                               return_value={"data_sources_access":
                                             {"enabled": True, "roles": []}}), \
             mock.patch("server_lib.auth.AuthDB.get_user",
                        lambda _uid: {"role": "user"}), \
             mock.patch("server_lib.auth.AuthDB.get_user_teams",
                        lambda _uid: []):
            out = json.loads(data_tools.tool_db_query(
                {"source": "braintest", "sql": "SELECT 1"}))
        self.assertIn("access denied", out["error"])


# ---------------------------------------------------------------------------
# access_mode ro/rw (DATA_SOURCES_V2_PLAN.md Phase 2)
# ---------------------------------------------------------------------------

class TestCheckStatementAllowed(unittest.TestCase):
    """Pure unit — layer 1 per mode. ro stays the shared SELECT/WITH check
    but a WRITE attempt must name the mode (the model should ask for an rw
    source, not rework the SQL); rw admits DML, never DDL (O3), and the
    single-statement rule holds in both modes."""

    def _c(self, sql, mode):
        return data_tools._check_statement_allowed(sql, mode)

    def test_ro_select_passes(self):
        self.assertIsNone(self._c("SELECT 1", "ro"))
        self.assertIsNone(self._c("WITH x AS (SELECT 1) SELECT * FROM x",
                                  "ro"))

    def test_ro_write_names_the_mode(self):
        for sql in ("INSERT INTO t VALUES (1)", "UPDATE t SET a=1",
                    "DELETE FROM t", "MERGE INTO t USING s ON 1=1",
                    "DROP TABLE t"):
            err = self._c(sql, "ro")
            self.assertIn("read-only", err)
            self.assertIn("rw source", err)

    def test_ro_garbage_keeps_select_error(self):
        self.assertIn("SELECT", self._c("EXPLAIN SELECT 1", "ro"))

    def test_rw_dml_passes(self):
        for sql in ("SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x",
                    "INSERT INTO t VALUES (1)", "UPDATE t SET a=1",
                    "DELETE FROM t WHERE a=1",
                    "MERGE INTO t USING s ON t.id=s.id WHEN MATCHED THEN "
                    "UPDATE SET a=1"):
            self.assertIsNone(self._c(sql, "rw"), sql)

    def test_rw_ddl_blocked(self):
        for sql in ("CREATE TABLE x (a INT)", "ALTER TABLE t ADD b INT",
                    "DROP TABLE t", "TRUNCATE t",
                    "GRANT ALL ON t TO PUBLIC", "REVOKE ALL ON t FROM x"):
            err = self._c(sql, "rw")
            self.assertIn("DDL", err, sql)

    def test_rw_multi_statement_blocked(self):
        self.assertIn("ONE statement",
                      self._c("INSERT INTO t VALUES (1); DROP TABLE t", "rw"))

    def test_rw_unknown_keyword_blocked(self):
        self.assertIn("allowed", self._c("VACUUM t", "rw"))

    def test_rw_empty(self):
        self.assertIn("empty", self._c("  ", "rw"))

    def test_source_access_mode_default_ro(self):
        self.assertEqual(data_tools._source_access_mode({}), "ro")
        self.assertEqual(data_tools._source_access_mode(
            {"access_mode": "RW"}), "rw")
        self.assertEqual(data_tools._source_access_mode(
            {"access_mode": "nonsense"}), "ro")


@unittest.skipUnless(_HAVE_PG, "local test postgres (braintest) not available")
class TestDbQueryRw(unittest.TestCase):
    """E5 live contract on postgres: an rw source really persists (commit
    proven across connections), reports mode+rowcount, still blocks DDL and
    multi-statements; the same write on an ro source dies in layer 1 with
    the mode error text."""

    @classmethod
    def setUpClass(cls):
        import psycopg2
        cls._table = "rw_scratch_" + uuid.uuid4().hex[:8]
        conn = psycopg2.connect(_PG_OWNER_DSN, connect_timeout=5)
        conn.autocommit = True
        conn.cursor().execute(
            f"CREATE TABLE {cls._table} (id INT PRIMARY KEY, txt TEXT)")
        conn.close()

    @classmethod
    def tearDownClass(cls):
        import psycopg2
        try:
            conn = psycopg2.connect(_PG_OWNER_DSN, connect_timeout=5)
            conn.autocommit = True
            conn.cursor().execute(f"DROP TABLE IF EXISTS {cls._table}")
            conn.close()
        except Exception:
            pass

    def setUp(self):
        self._sid = "datatools-rwtest-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent(),
            current_user_id="__system__"))
        self.enterContext(mock.patch.object(
            data_tools, "_data_sources", return_value=_TEST_SOURCES))

    def _q(self, **args):
        return json.loads(data_tools.tool_db_query(args))

    def test_insert_select_roundtrip(self):
        ins = self._q(source="braintest_rw",
                      sql=f"INSERT INTO {self._table} (id, txt) "
                          f"VALUES (1, 'hello')")
        self.assertEqual(ins.get("mode"), "rw")
        self.assertEqual(ins.get("rowcount"), 1)
        # Read back on a FRESH connection — proves the commit, not just the
        # session-local view.
        sel = self._q(source="braintest_rw",
                      sql=f"SELECT txt FROM {self._table} WHERE id = 1")
        self.assertIn("hello", sel["result"])
        self.assertEqual(sel.get("mode"), "rw")
        upd = self._q(source="braintest_rw",
                      sql=f"UPDATE {self._table} SET txt='bye' WHERE id=1")
        self.assertEqual(upd.get("rowcount"), 1)

    def test_ddl_blocked_on_rw(self):
        out = self._q(source="braintest_rw",
                      sql=f"DROP TABLE {self._table}")
        self.assertIn("DDL", out["error"])

    def test_multi_statement_blocked_on_rw(self):
        out = self._q(source="braintest_rw",
                      sql=f"INSERT INTO {self._table} VALUES (9,'x'); "
                          f"DELETE FROM {self._table}")
        self.assertIn("ONE statement", out["error"])

    def test_write_on_ro_source_names_mode(self):
        out = self._q(source="braintest",
                      sql=f"INSERT INTO {self._table} VALUES (2,'nope')")
        self.assertIn("read-only", out["error"])
        self.assertIn("rw source", out["error"])

    def test_ro_result_has_no_rw_mode(self):
        out = self._q(source="braintest", sql="SELECT 1 AS one")
        self.assertNotIn("mode", out)
        self.assertIn("read-only", out["read_only"])


# ---------------------------------------------------------------------------
# Scoping-Kern: data_source_scope + Tabellen-Whitelist (Phase 3, E6/E8)
# ---------------------------------------------------------------------------

class TestCheckTablesAllowed(unittest.TestCase):
    """E6 pure unit — sqlglot-backed hard table whitelist. schema.table and
    bare table match BOTH ways, CTE names are not table refs,
    information_schema (and mssql sys) stay always readable (O2), and
    unparsable SQL fails CLOSED naming the allowed tables."""

    def _c(self, sql, allowed, stype="postgres"):
        return data_tools._check_tables_allowed(sql, stype, allowed)

    def test_bare_and_schema_qualified_match_both_ways(self):
        self.assertIsNone(self._c("SELECT * FROM positionen", ["positionen"]))
        self.assertIsNone(self._c("SELECT * FROM public.positionen",
                                  ["positionen"]))
        self.assertIsNone(self._c("SELECT * FROM positionen",
                                  ["public.positionen"]))

    def test_unlisted_table_blocked_and_names_whitelist(self):
        err = self._c("SELECT * FROM andere", ["positionen"])
        self.assertIn("andere", err)
        self.assertIn("positionen", err)

    def test_join_with_unlisted_table_blocked(self):
        err = self._c("SELECT a.* FROM positionen a JOIN andere b "
                      "ON a.id=b.id", ["positionen"])
        self.assertIn("andere", err)

    def test_subquery_unlisted_blocked(self):
        self.assertIsNotNone(self._c(
            "SELECT * FROM (SELECT * FROM andere) s", ["positionen"]))

    def test_insert_target_checked(self):
        self.assertIsNotNone(self._c("INSERT INTO andere VALUES (1)",
                                     ["positionen"]))
        self.assertIsNone(self._c("INSERT INTO positionen VALUES (1)",
                                  ["positionen"]))

    def test_cte_name_is_not_a_table_ref(self):
        self.assertIsNone(self._c(
            "WITH x AS (SELECT * FROM positionen) SELECT * FROM x",
            ["positionen"]))
        # Even a CTE named like an UNLISTED table is fine — it's a CTE.
        self.assertIsNone(self._c(
            "WITH andere AS (SELECT * FROM positionen) "
            "SELECT * FROM andere", ["positionen"]))

    def test_information_schema_always_readable(self):
        self.assertIsNone(self._c(
            "SELECT table_name FROM information_schema.tables",
            ["positionen"]))

    def test_mssql_sys_allowed_only_for_mssql(self):
        self.assertIsNone(self._c("SELECT name FROM sys.databases",
                                  ["positionen"], stype="mssql"))
        self.assertIsNotNone(self._c("SELECT name FROM sys.databases",
                                     ["positionen"], stype="postgres"))

    def test_mssql_bracket_identifiers(self):
        self.assertIsNone(self._c("SELECT [x] FROM [dbo].[ITEMDATA]",
                                  ["itemdata"], stype="mssql"))

    def test_unparsable_fails_closed(self):
        err = self._c("SELECT * FROM (((", ["positionen"])
        self.assertIsNotNone(err)
        self.assertIn("positionen", err)


class TestDataSourceScopeGate(unittest.TestCase):
    """E8 tool gate, pure unit (policy mocked open, no DB needed): scope
    None = deny for normal users with a config hint; unscoped source names
    only the ENABLED sources; a scoped source passes through to the
    connection stage; [] means all tables; __system__ bypasses entirely.
    Uses dead-host sources so 'connection failed' PROVES the gates passed."""

    def setUp(self):
        self.enterContext(mock.patch.object(
            data_tools, "data_access_allowed", return_value=(True, "")))
        self.enterContext(mock.patch.object(
            data_tools, "_data_sources", return_value=_TEST_SOURCES))

    def _q(self, scope, user="u1", **args):
        with request_context(current_session_id="scope-test",
                             current_agent=_FakeAgent(),
                             current_user_id=user,
                             data_source_scope=scope):
            return json.loads(data_tools.tool_db_query(args))

    def test_no_scope_denies_normal_user_with_hint(self):
        out = self._q(None, source="dead_db", sql="SELECT 1")
        self.assertIn("no data sources are enabled", out["error"])
        self.assertIn("Projekt-Einstellungen", out["error"])
        self.assertIn("right panel", out["error"])

    def test_source_not_in_scope_lists_enabled(self):
        out = self._q({"braintest": []}, source="dead_db", sql="SELECT 1")
        self.assertIn("not enabled in this context", out["error"])
        self.assertIn("braintest", out["error"])
        self.assertNotIn("connection failed", out["error"])

    def test_scoped_source_reaches_connection(self):
        out = self._q({"dead_db": []}, source="dead_db", sql="SELECT 1")
        self.assertIn("connection failed", out["error"])

    def test_empty_tables_means_all(self):
        out = self._q({"dead_db": []}, source="dead_db",
                      sql="SELECT * FROM was_auch_immer")
        self.assertIn("connection failed", out["error"])

    def test_table_whitelist_blocks_before_connect(self):
        out = self._q({"dead_db": ["positionen"]}, source="dead_db",
                      sql="SELECT * FROM andere")
        self.assertIn("not allowed in this context", out["error"])
        self.assertNotIn("connection failed", out["error"])

    def test_table_whitelist_pass_reaches_connection(self):
        out = self._q({"dead_db": ["positionen"]}, source="dead_db",
                      sql="SELECT * FROM positionen")
        self.assertIn("connection failed", out["error"])

    def test_information_schema_passes_whitelist(self):
        out = self._q({"dead_db": ["positionen"]}, source="dead_db",
                      sql="SELECT table_name FROM information_schema.tables")
        self.assertIn("connection failed", out["error"])

    def test_unparsable_sql_fails_closed_under_whitelist(self):
        out = self._q({"dead_db": ["positionen"]}, source="dead_db",
                      sql="SELECT * FROM (((")
        self.assertIn("error", out)
        self.assertNotIn("connection failed", out["error"])

    def test_system_bypasses_scope(self):
        out = self._q(None, user="__system__", source="dead_db",
                      sql="SELECT 1")
        self.assertIn("connection failed", out["error"])

    def test_scope_check_runs_before_mode_check(self):
        # Unscoped source + write SQL → the SCOPE error, not the mode error
        # (E1 order: policy → scope → mode → tables).
        out = self._q({"braintest": []}, source="dead_db",
                      sql="INSERT INTO x VALUES (1)")
        self.assertIn("not enabled in this context", out["error"])


# ---------------------------------------------------------------------------
# Projekt-Scope-Verdrahtung (Phase 4, E8)
# ---------------------------------------------------------------------------

class TestProjectScopeWiring(unittest.TestCase):
    """E8: apply_domain_context derives data_source_scope from project.json →
    data_sources ([{name, tables}] → {name: [tables]}; missing/empty = None,
    no silent global fallback); build_tool_context snapshots the field and
    _apply_bg_context rehydrates it — project scheduler runs inherit the
    project scope, sched sessions without a project stay None (O1)."""

    def _apply(self, pcfg):
        from engine.context import get_request_context
        with request_context():
            with mock.patch.object(brain.ProjectManager, "get_project",
                                   return_value=pcfg), \
                 mock.patch("server_lib.auth.AuthDB.get_user_teams",
                            return_value=[]):
                brain.apply_domain_context(agent_id="main", project="p1",
                                           user_id="u1")
            return get_request_context().data_source_scope

    def test_project_config_becomes_scope(self):
        scope = self._apply({"data_sources": [
            {"name": "braintest", "tables": ["positionen"]},
            {"name": "braintest_rw", "tables": []}]})
        self.assertEqual(scope, {"braintest": ["positionen"],
                                 "braintest_rw": []})

    def test_project_without_sources_is_none(self):
        self.assertIsNone(self._apply({}))
        self.assertIsNone(self._apply({"data_sources": []}))

    def test_no_project_leaves_scope_untouched(self):
        from engine.context import get_request_context
        with request_context(data_source_scope={"x": []}):
            with mock.patch("server_lib.auth.AuthDB.get_user_teams",
                            return_value=[]):
                brain.apply_domain_context(agent_id="main", project="",
                                           user_id="u1")
            self.assertEqual(get_request_context().data_source_scope,
                             {"x": []})

    def test_tool_context_roundtrip_to_bg(self):
        from engine.context import get_request_context
        from handlers import sidecar_proxy
        with request_context(data_source_scope={"braintest": ["positionen"]}):
            tc = brain.build_tool_context(session_id="s1", agent_id="main",
                                          user_id="u1")
        self.assertEqual(tc["data_source_scope"],
                         {"braintest": ["positionen"]})
        with request_context():
            sidecar_proxy._apply_bg_context(tc)
            self.assertEqual(get_request_context().data_source_scope,
                             {"braintest": ["positionen"]})
        # No scope in the ctx dict (sched session without project) → None.
        tc2 = dict(tc)
        tc2.pop("data_source_scope")
        with request_context():
            sidecar_proxy._apply_bg_context(tc2)
            self.assertIsNone(get_request_context().data_source_scope)


# ---------------------------------------------------------------------------
# db_query — mssql (DATA_SOURCES_V2_PLAN.md Phase 1)
# ---------------------------------------------------------------------------

_MS_DSN = "mssql://brain_ro_ms:BrainRo!Test1@localhost:1433/braintest_ms"


class TestMssqlConnStr(unittest.TestCase):
    """Pure unit (no server needed) — the ODBC string must match the
    bank-verified specimen (plan Anhang B): `SERVER=host,port` with a COMMA
    and NO Encrypt/TrustServerCertificate params; Driver 17's `Encrypt=no`
    default is the only wire verified in the target network."""

    def _cs(self, dsn, **options):
        return data_tools._mssql_odbc_conn_str(
            {"name": "t", "type": "mssql", "dsn": dsn, "options": options})

    def test_specimen_shape(self):
        cs = self._cs("mssql://u:pw@dbhost:1433/mydb")
        self.assertEqual(
            cs, "DRIVER={ODBC Driver 17 for SQL Server};"
                "SERVER=dbhost,1433;DATABASE=mydb;UID={u};PWD={pw}")

    def test_no_encrypt_params_ever(self):
        cs = self._cs("mssql://u:pw@dbhost:1433/mydb")
        self.assertNotIn("Encrypt", cs)
        self.assertNotIn("TrustServerCertificate", cs)

    def test_default_port_1433_with_comma(self):
        self.assertIn("SERVER=dbhost,1433;",
                      self._cs("mssql://u:p@dbhost/db"))

    def test_driver_override(self):
        cs = self._cs("mssql://u:p@h/d", odbc_driver="Custom Driver X")
        self.assertIn("DRIVER={Custom Driver X}", cs)

    def test_windows_auth_no_credentials(self):
        cs = self._cs("mssql://h/d", windows_auth=True)
        self.assertIn("Trusted_Connection=yes", cs)
        self.assertNotIn("UID", cs)
        self.assertNotIn("PWD", cs)

    def test_url_encoded_password_decoded_and_braced(self):
        # ';'/'@' in the secret must survive as ONE ODBC value.
        cs = self._cs("mssql://u:p%40ss%3Bx@h:1433/d")
        self.assertIn("PWD={p@ss;x}", cs)

    def test_missing_database_or_host_fails(self):
        with self.assertRaises(ValueError):
            self._cs("mssql://u:p@h:1433")
        with self.assertRaises(ValueError):
            self._cs("mssql:///d")

    def test_missing_user_without_windows_auth_fails(self):
        with self.assertRaises(ValueError):
            self._cs("mssql://h:1433/d")


def _ms_available() -> bool:
    try:
        conn, _cur = data_tools._connect_mssql(
            {"name": "probe", "type": "mssql", "dsn": _MS_DSN,
             "options": {"connect_timeout": 2}})
        conn.close()
        return True
    except Exception:
        return False


_HAVE_MS = _ms_available()

_MS_TEST_SOURCES = [
    {"name": "braintest_ms", "type": "mssql", "dsn": _MS_DSN,
     "options": {"statement_timeout_ms": 60000, "connect_timeout": 5}},
    {"name": "dead_ms", "type": "mssql",
     "dsn": "mssql://x:x@localhost:59999/nope",
     "options": {"connect_timeout": 2}},
]


@unittest.skipUnless(_HAVE_MS, "local test MSSQL (braintest_ms) not available "
                     "— scripts/setup_mssql_testdb.py")
class TestDbQueryMssql(unittest.TestCase):
    """Same contract as the postgres suite, adjusted for E4: MSSQL has NO
    session read-only, so the layers are 1 (statement gate) + 3 (the
    brain_ro_ms login holds db_datareader ONLY) — layer 3 is proven directly
    against the DB grant, and the result's read_only note must say so
    honestly instead of claiming a read-only session."""

    def setUp(self):
        self._sid = "datatools-mstest-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent(),
            current_user_id="__system__"))
        self.enterContext(mock.patch.object(
            data_tools, "_data_sources", return_value=_MS_TEST_SOURCES))

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
        out = self._q(source="braintest_ms",
                      sql="SELECT COUNT(*) AS n, SUM(stueck) AS s "
                          "FROM positionen")
        self.assertEqual(out["row_count"], 1)
        self.assertIn("| 50000 | 12525000 |", out["result"])

    def test_readonly_note_is_honest(self):
        out = self._q(source="braintest_ms", sql="SELECT 1 AS one")
        self.assertIn("db_datareader", out["read_only"])
        self.assertNotIn("session read-only (layer 2)", out["read_only"])

    def test_rejects_insert_layer1(self):
        out = self._q(source="braintest_ms",
                      sql="INSERT INTO positionen (filiale_id, isin, stueck, "
                          "kurs) VALUES (1,'X',1,1)")
        self.assertIn("error", out)
        self.assertIn("SELECT", out["error"])

    def test_layer3_grant_blocks_direct_insert(self):
        """Layer-3 proof, independent of layer 1: a direct INSERT on the
        tool's OWN connection (db_datareader login) must die at the DB
        grant — there is no session layer to catch it first."""
        src = data_tools._resolve_db_source("braintest_ms")
        conn, cur = data_tools._connect_mssql(src)
        try:
            with self.assertRaises(Exception) as ctx:
                cur.execute("INSERT INTO positionen (filiale_id, isin, "
                            "stueck, kurs) VALUES (1,'X',1,1)")
            self.assertIn("permission", str(ctx.exception).lower())
        finally:
            conn.close()

    def test_rejects_multi_statement(self):
        out = self._q(source="braintest_ms",
                      sql="SELECT 1; DELETE FROM positionen")
        self.assertIn("error", out)
        self.assertIn("ONE statement", out["error"])

    def test_unknown_source_lists_available(self):
        out = self._q(source="nope", sql="SELECT 1")
        self.assertIn("error", out)
        self.assertIn("braintest_ms", out["error"])

    def test_dead_connection_clean_error(self):
        out = self._q(source="dead_ms", sql="SELECT 1")
        self.assertIn("error", out)
        self.assertIn("connection failed", out["error"])

    def test_out_writes_full_csv(self):
        out = self._q(source="braintest_ms",
                      sql="SELECT * FROM positionen WHERE filiale_id = 1 "
                          "ORDER BY id",
                      out="fil1_ms.csv")
        self.assertEqual(out["saved"]["rows"], 2000)
        with open(out["saved"]["path"], encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), 2001)


# ---------------------------------------------------------------------------
# rest_query (DATA_SOURCES_V2_PLAN.md Phase 6, E10)
# ---------------------------------------------------------------------------

import http.server  # noqa: E402
import threading  # noqa: E402
import time as _time  # noqa: E402


class _RestStub(http.server.BaseHTTPRequestHandler):
    """Local stub API: /api/v1/items (GET list, POST 201), /api/echo-auth
    (echoes request headers), /api/big (oversized JSON), /api/slow (2s)."""

    def log_message(self, *a):  # silent
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/v1/items":
            self._json(200, [{"id": 1, "name": "alpha"},
                             {"id": 2, "name": "beta"}])
        elif p == "/api/echo-auth":
            self._json(200, {k: v for k, v in self.headers.items()})
        elif p == "/api/big":
            self._json(200, {"blob": "x" * 5000})
        elif p == "/api/slow":
            _time.sleep(2)
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/v1/items":
            self._json(201, {"created": True})
        else:
            self._json(404, {"error": "not found"})


class TestRestQuery(unittest.TestCase):
    """E10 contract against a local stub API: base_url confinement (absolute
    URLs / '..' / '//' / encoded '..' rejected), allowed_paths (config) and
    scope paths (context) as prefix whitelists, ro = GET/HEAD only, auth
    header injection, response cap, clean timeout, HTTP errors as results."""

    @classmethod
    def setUpClass(cls):
        cls._httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _RestStub)
        cls._port = cls._httpd.server_address[1]
        cls._thread = threading.Thread(
            target=cls._httpd.serve_forever, daemon=True)
        cls._thread.start()
        base = f"http://127.0.0.1:{cls._port}"
        cls._sources = [
            {"name": "apitest", "type": "rest", "base_url": base,
             "access_mode": "ro",
             "auth": {"kind": "bearer", "secret": "tok123"},
             "options": {"timeout_s": 5, "max_response_kb": 256}},
            {"name": "api_rw", "type": "rest", "base_url": base,
             "access_mode": "rw",
             "auth": {"kind": "header", "secret": "k-9",
                      "header_name": "X-Api-Key"}},
            {"name": "api_wl", "type": "rest", "base_url": base,
             "access_mode": "ro", "auth": {"kind": "none"},
             "allowed_paths": ["/api/v1/"]},
            {"name": "api_tiny", "type": "rest", "base_url": base,
             "access_mode": "ro", "auth": {"kind": "none"},
             "options": {"max_response_kb": 1}},
            {"name": "api_slow", "type": "rest", "base_url": base,
             "access_mode": "ro", "auth": {"kind": "none"},
             "options": {"timeout_s": 1}},
        ]

    @classmethod
    def tearDownClass(cls):
        cls._httpd.shutdown()
        cls._httpd.server_close()

    def setUp(self):
        self._sid = "datatools-resttest-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent(),
            current_user_id="__system__"))
        self.enterContext(mock.patch.object(
            data_tools, "_data_sources",
            return_value=self._sources + _TEST_SOURCES))

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
        return json.loads(data_tools.tool_rest_query(args))

    def test_get_json_and_auth_header(self):
        out = self._q(source="apitest", path="/api/echo-auth")
        self.assertEqual(out["status"], 200)
        self.assertIn("Bearer tok123", out["result"])

    def test_header_auth_kind(self):
        out = self._q(source="api_rw", path="/api/echo-auth")
        self.assertIn("k-9", out["result"])
        self.assertIn("X-Api-Key", out["result"])

    def test_ro_blocks_post_with_mode_text(self):
        out = self._q(source="apitest", path="/api/v1/items", method="POST",
                      body={"name": "x"})
        self.assertIn("read-only", out["error"])
        self.assertIn("rw source", out["error"])

    def test_rw_allows_post(self):
        out = self._q(source="api_rw", path="/api/v1/items", method="POST",
                      body={"name": "x"})
        self.assertEqual(out["status"], 201)
        self.assertEqual(out.get("mode"), "rw")
        self.assertIn("created", out["result"])

    def test_path_escapes_blocked(self):
        for bad in ("../etc", "/api/../secret", "//evil.example/x",
                    "https://evil.example/x", "/api/%2e%2e/secret"):
            out = self._q(source="apitest", path=bad)
            self.assertIn("error", out, bad)
            self.assertNotIn("status", out, bad)

    def test_source_allowed_paths_prefix(self):
        ok = self._q(source="api_wl", path="/api/v1/items")
        self.assertEqual(ok["status"], 200)
        bad = self._q(source="api_wl", path="/api/echo-auth")
        self.assertIn("allowed paths", bad["error"])

    def test_scope_paths_prefix(self):
        with mock.patch.object(data_tools, "data_access_allowed",
                               return_value=(True, "")):
            with request_context(
                    current_session_id=self._sid,
                    current_agent=_FakeAgent(), current_user_id="u1",
                    data_source_scope={"apitest": ["/api/v1/"]}):
                ok = self._q(source="apitest", path="/api/v1/items")
                bad = self._q(source="apitest", path="/api/echo-auth")
                other = self._q(source="api_rw", path="/api/v1/items")
        self.assertEqual(ok["status"], 200)
        self.assertIn("allowed paths", bad["error"])
        self.assertIn("not enabled in this context", other["error"])

    def test_response_cap_truncates(self):
        out = self._q(source="api_tiny", path="/api/big")
        self.assertEqual(out["status"], 200)
        self.assertIn("truncated", out)
        self.assertLessEqual(len(out["result"]), 1100)

    def test_timeout_clean_error(self):
        out = self._q(source="api_slow", path="/api/slow")
        self.assertIn("timeout", out["error"])

    def test_http_error_status_is_a_result(self):
        out = self._q(source="apitest", path="/api/nope")
        self.assertEqual(out["status"], 404)
        self.assertIn("not found", out["result"])
        self.assertIn("note", out)

    def test_out_json_and_csv(self):
        out = self._q(source="apitest", path="/api/v1/items",
                      out="items.json")
        self.assertTrue(out["saved"]["path"].endswith("items.json"))
        with open(out["saved"]["path"], encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 2)
        out2 = self._q(source="apitest", path="/api/v1/items",
                       out="items.csv")
        with open(out2["saved"]["path"], encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), 3)  # header + 2 rows
        self.assertIn("id", lines[0])

    def test_db_query_on_rest_source_redirects(self):
        out = json.loads(data_tools.tool_db_query(
            {"source": "apitest", "sql": "SELECT 1"}))
        self.assertIn("rest_query", out["error"])

    def test_rest_query_on_sql_source_redirects(self):
        out = self._q(source="dead_db", path="/x")
        self.assertIn("db_query", out["error"])


if __name__ == "__main__":
    unittest.main()
