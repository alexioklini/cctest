#!/usr/bin/env python3
"""One-shot setup of the local MSSQL test DB (DATA_SOURCES_V2_PLAN.md Phase 1).

Creates `braintest_ms` as the MSSQL twin of the postgres `braintest`:
same 50k `positionen` rows (copied 1:1 from the local postgres, so both test
suites assert the same totals), a read-only login `brain_ro_ms` holding
db_datareader ONLY (layer 3 of the read-only contract — MSSQL has no session
read-only), and SA as the owner login for write-path proofs.

Deliberately runs on the SAME stack the bank network verified (pyodbc +
"ODBC Driver 17 for SQL Server", plan Anhang B) — a different driver here
would validate the wrong path (O4).

Prereq — a local MSSQL, e.g. Docker (Apple Silicon needs amd64 emulation):
  docker run --platform linux/amd64 -e ACCEPT_EULA=Y \
    -e MSSQL_SA_PASSWORD='BrainSa!Test1' -p 1433:1433 \
    --name braintest-mssql -d mcr.microsoft.com/mssql/server:2022-latest

Run: python3 scripts/setup_mssql_testdb.py
Env: MSSQL_SA_PASSWORD (default BrainSa!Test1), MSSQL_HOST/PORT
"""

import os
import sys

import pyodbc
import psycopg2

SA_PASSWORD = os.environ.get("MSSQL_SA_PASSWORD", "BrainSa!Test1")
HOST = os.environ.get("MSSQL_HOST", "localhost")
PORT = int(os.environ.get("MSSQL_PORT", "1433"))
DRIVER = os.environ.get("MSSQL_DRIVER", "ODBC Driver 17 for SQL Server")

PG_DSN = "postgresql://brain_ro:brain_ro_test@localhost:5432/braintest"
RO_LOGIN = "brain_ro_ms"
RO_PASSWORD = "BrainRo!Test1"  # mirrored in tests/test_data_tools.py _MS_DSN
DB = "braintest_ms"


def _connect(database: str) -> pyodbc.Connection:
    # Specimen shape: SERVER=host,port (comma), no Encrypt params.
    conn = pyodbc.connect(
        f"DRIVER={{{DRIVER}}};SERVER={HOST},{PORT};DATABASE={database};"
        f"UID=sa;PWD={SA_PASSWORD}", timeout=10, autocommit=True)
    conn.timeout = 120
    return conn


def main() -> int:
    master = _connect("master")
    cur = master.cursor()
    cur.execute(f"IF DB_ID('{DB}') IS NULL CREATE DATABASE [{DB}]")
    cur.execute(
        f"IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = "
        f"'{RO_LOGIN}') CREATE LOGIN [{RO_LOGIN}] WITH PASSWORD = "
        f"'{RO_PASSWORD}', CHECK_POLICY = OFF")
    master.close()

    db = _connect(DB)
    cur = db.cursor()
    # Read-only principal: db_datareader ONLY (the E4 layer-3 recipe).
    cur.execute(
        f"IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = "
        f"'{RO_LOGIN}') CREATE USER [{RO_LOGIN}] FOR LOGIN [{RO_LOGIN}]")
    cur.execute(f"ALTER ROLE db_datareader ADD MEMBER [{RO_LOGIN}]")
    cur.execute("IF OBJECT_ID('positionen') IS NOT NULL DROP TABLE positionen")
    cur.execute(
        "CREATE TABLE positionen ("
        " id INT NOT NULL PRIMARY KEY,"
        " filiale_id INT NOT NULL,"
        " isin NVARCHAR(20) NOT NULL,"
        " stueck INT NOT NULL,"
        " kurs DECIMAL(18,6) NOT NULL)")

    pg = psycopg2.connect(PG_DSN, connect_timeout=5)
    pg_cur = pg.cursor(name="seed_ms")  # server-side: stream, don't load 50k
    pg_cur.itersize = 5000
    pg_cur.execute(
        "SELECT id, filiale_id, isin, stueck, kurs FROM positionen ORDER BY id")
    cur.fast_executemany = True
    total = 0
    while True:
        rows = pg_cur.fetchmany(5000)
        if not rows:
            break
        cur.executemany(
            "INSERT INTO positionen (id, filiale_id, isin, stueck, kurs) "
            "VALUES (?, ?, ?, ?, ?)", rows)
        total += len(rows)
        print(f"  {total} rows...", flush=True)
    pg.close()

    cur.execute("SELECT COUNT(*), SUM(stueck) FROM positionen")
    n, s = cur.fetchone()
    db.close()
    print(f"braintest_ms ready: {n} rows, SUM(stueck)={s} "
          f"(expected 50000 / 12525000)")
    if (n, int(s)) != (50000, 12525000):
        print("MISMATCH vs postgres braintest — investigate", file=sys.stderr)
        return 1
    print(f"read-only DSN for config.json / tests:\n"
          f"  mssql://{RO_LOGIN}:{RO_PASSWORD}@{HOST}:{PORT}/{DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
