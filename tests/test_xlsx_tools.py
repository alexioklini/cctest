"""Tests for the deterministic XLSX toolset (xlsx_inspect / xlsx_query /
xlsx_create / xlsx_edit, engine/tools/xlsx_tools.py) + a byte-stability pin
on doc_convert._extract_xlsx.

WHY the pin comes first: xlsx_tools reuses the v9.261.0 placeholder-column
trim, which gets factored OUT of _extract_xlsx into
doc_convert._trim_placeholder_columns. The mining daemon's companion-`.md`
output must stay byte-stable across that refactor (changing it would re-embed
every project doc) — so the expected strings below were captured by PROBING
the PRE-refactor _extract_xlsx (2026-07-02) and the refactor must reproduce
them exactly.

The synthetic fixture mirrors the production case that motivated the toolset
(chats 2cb94154 / 98cceac2): a 2-sheet workbook, market orders in sheet 1,
partial executions in sheet 2, joined via MARKTORDERNUMMER.

Run: python3 -m unittest tests.test_xlsx_tools -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid  # noqa: F401  (used by the tool fixtures below)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openpyxl  # noqa: E402

from engine import doc_convert  # noqa: E402


def _build_orders_workbook(path):
    """The marktorder shape: orders + partial executions keyed by
    MARKTORDERNUMMER (MO-1 has 2 executions, MO-2 has 1)."""
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Orders"
    ws1.append(["MARKTORDERNUMMER", "NAME", "STUECK", "LIMIT"])
    ws1.append(["MO-1", "Alice", 100, 25.5])
    ws1.append(["MO-2", "Bob", 50, None])
    ws2 = wb.create_sheet("Ausfuehrungen")
    ws2.append(["MARKTORDERNUMMER", "DATUM", "STUECK", "KURS"])
    ws2.append(["MO-1", "2026-01-02", 60, 25.4])
    ws2.append(["MO-1", "2026-01-03", 40, 25.6])
    ws2.append(["MO-2", "2026-01-04", 50, 11.0])
    wb.save(path)


def _build_placeholder_workbook(path):
    """The v9.261.0 failure shape: real columns A/B/C plus an auto-named
    'Spalte<N>' placeholder tail that carries headers but no data."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Daten"
    ws.append(["A", "B", "C"] + [f"Spalte{i}" for i in range(4, 40)])
    ws.append([1, 2, 3] + [None] * 36)
    ws.append([4, None, 6] + [None] * 36)
    wb.save(path)


class _XlsxFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="xlsxtools_test_")
        self.orders_path = os.path.join(self._tmp, "orders.xlsx")
        self.placeholder_path = os.path.join(self._tmp, "placeholder.xlsx")
        _build_orders_workbook(self.orders_path)
        _build_placeholder_workbook(self.placeholder_path)


class TestExtractXlsxBytePin(_XlsxFixture):
    """Byte-stability pin for _extract_xlsx across the trim refactor.
    Expected values probed against the PRE-refactor code — do not 'fix' them
    to match new behavior; if they fail, the refactor changed mining output."""

    ORDERS_EXPECTED = (
        "# orders\n\n\n"
        "## Sheet: Orders\n\n"
        "| MARKTORDERNUMMER | NAME | STUECK | LIMIT |\n"
        "|---|---|---|---|\n"
        "| MO-1 | Alice | 100 | 25.5 |\n"
        "| MO-2 | Bob | 50 |  |\n\n"
        "## Sheet: Ausfuehrungen\n\n"
        "| MARKTORDERNUMMER | DATUM | STUECK | KURS |\n"
        "|---|---|---|---|\n"
        "| MO-1 | 2026-01-02 | 60 | 25.4 |\n"
        "| MO-1 | 2026-01-03 | 40 | 25.6 |\n"
        "| MO-2 | 2026-01-04 | 50 | 11 |\n"
    )

    PLACEHOLDER_EXPECTED = (
        "# placeholder\n\n\n"
        "## Sheet: Daten\n\n"
        "| A | B | C |\n"
        "|---|---|---|\n"
        "| 1 | 2 | 3 |\n"
        "| 4 |  | 6 |\n"
    )

    def test_orders_caps_false_and_true_identical_to_pin(self):
        for caps in (False, True):
            text, err = doc_convert._extract_xlsx(self.orders_path, caps=caps)
            self.assertIsNone(err)
            self.assertEqual(text, self.ORDERS_EXPECTED)

    def test_placeholder_tail_trimmed_identical_to_pin(self):
        for caps in (False, True):
            text, err = doc_convert._extract_xlsx(
                self.placeholder_path, caps=caps)
            self.assertIsNone(err)
            self.assertEqual(text, self.PLACEHOLDER_EXPECTED)


import brain  # noqa: E402  (loads TOOL_DISPATCH etc.; also warms lazy imports)
from engine.context import request_context  # noqa: E402
from engine.tools import xlsx_tools  # noqa: E402


class _FakeAgent:
    agent_id = "main"


class _ToolFixture(_XlsxFixture):
    """Adds a request context (unique session id → fresh artifact folder, same
    pattern as test_file_tools_characterization) on top of the workbook tmpdir."""

    def setUp(self):
        super().setUp()
        self._prev_cwd = os.getcwd()
        os.chdir(self._tmp)
        self._sid = "xlsxtools-test-" + uuid.uuid4().hex[:8]
        self.enterContext(request_context(
            current_session_id=self._sid, current_agent=_FakeAgent()))

    def tearDown(self):
        os.chdir(self._prev_cwd)
        # Don't leave per-test artifact folders behind in the repo (the sid is
        # unique per test, so this only ever removes what THIS test created).
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


class TestXlsxInspect(_ToolFixture):
    def test_profile_and_join_key(self):
        out = json.loads(xlsx_tools.tool_xlsx_inspect(
            {"path": self.orders_path}))
        rep = out["report"]
        self.assertIn("Sheet: Orders", rep)
        self.assertIn("Sheet: Ausfuehrungen", rep)
        self.assertIn("2 data rows", rep)          # Orders
        self.assertIn("3 data rows", rep)          # Ausfuehrungen
        # join-key candidate detected with full overlap
        self.assertIn("MARKTORDERNUMMER", rep)
        self.assertIn("likely JOIN key", rep)
        # copy-paste schema block with sanitized names
        self.assertIn("Tables for xlsx_query:", rep)
        self.assertIn("orders(marktordernummer", rep)

    def test_placeholder_columns_absent(self):
        out = json.loads(xlsx_tools.tool_xlsx_inspect(
            {"path": self.placeholder_path}))
        rep = out["report"]
        self.assertIn("3 columns", rep)
        self.assertNotIn("Spalte4", rep)

    def test_missing_file_is_error(self):
        out = json.loads(xlsx_tools.tool_xlsx_inspect({"path": "nope.xlsx"}))
        self.assertIn("File not found", out["error"])


class TestXlsxQuery(_ToolFixture):
    def test_join_across_sheets(self):
        out = json.loads(xlsx_tools.tool_xlsx_query({
            "path": self.orders_path,
            "sql": ("SELECT o.marktordernummer, o.name, a.datum, a.kurs "
                    "FROM orders o JOIN ausfuehrungen a "
                    "ON a.marktordernummer = o.marktordernummer "
                    "ORDER BY o.marktordernummer, a.datum")}))
        self.assertEqual(out["row_count"], 3)
        self.assertIn("| MO-1 | Alice | 2026-01-02 | 25.4 |", out["result"])
        self.assertIn("| MO-2 | Bob | 2026-01-04 |", out["result"])

    def test_aggregate(self):
        out = json.loads(xlsx_tools.tool_xlsx_query({
            "path": self.orders_path,
            "sql": ("SELECT marktordernummer, SUM(stueck) AS s FROM "
                    "ausfuehrungen GROUP BY marktordernummer "
                    "ORDER BY marktordernummer")}))
        self.assertEqual(out["row_count"], 2)
        self.assertIn("| MO-1 | 100 |", out["result"])

    def test_select_only_rejections(self):
        for bad in ("INSERT INTO orders VALUES (1)",
                    "UPDATE orders SET name='x'",
                    "DROP TABLE orders",
                    "PRAGMA table_info(orders)",
                    "ATTACH DATABASE 'x' AS y",
                    "SELECT 1; DROP TABLE orders"):
            out = json.loads(xlsx_tools.tool_xlsx_query(
                {"path": self.orders_path, "sql": bad}))
            self.assertIn("error", out, bad)

    def test_cte_allowed(self):
        out = json.loads(xlsx_tools.tool_xlsx_query({
            "path": self.orders_path,
            "sql": "WITH x AS (SELECT * FROM orders) SELECT COUNT(*) FROM x"}))
        self.assertEqual(out["row_count"], 1)

    def test_sql_error_echoes_schema(self):
        out = json.loads(xlsx_tools.tool_xlsx_query(
            {"path": self.orders_path, "sql": "SELECT nope FROM orders"}))
        self.assertIn("error", out)
        self.assertIn("Tables for xlsx_query:", out["error"])
        self.assertIn("orders(", out["error"])

    def test_out_writes_full_csv(self):
        out = json.loads(xlsx_tools.tool_xlsx_query({
            "path": self.orders_path,
            "sql": "SELECT * FROM ausfuehrungen",
            "out": "result.csv"}))
        saved = out["saved"]
        self.assertEqual(saved["rows"], 3)
        with open(saved["path"], encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
        self.assertEqual(len(lines), 4)  # header + 3 rows

    def test_multi_file_join_with_prefixes(self):
        second = os.path.join(self._tmp, "orders_neu.xlsx")
        _build_orders_workbook(second)
        out = json.loads(xlsx_tools.tool_xlsx_query({
            "paths": [self.orders_path, second],
            "sql": ("SELECT COUNT(*) FROM orders_orders a "
                    "JOIN orders_neu_orders b "
                    "ON a.marktordernummer = b.marktordernummer")}))
        self.assertEqual(out["row_count"], 1)
        self.assertIn("| 2 |", out["result"])


class TestXlsxCreate(_ToolFixture):
    def test_inline_table_roundtrip(self):
        out = json.loads(xlsx_tools.tool_xlsx_create({
            "path": "report.xlsx",
            "spec": {"sheets": [{
                "name": "Umsatz",
                "columns": [{"name": "Monat"},
                            {"name": "Betrag", "format": "eur"}],
                "rows": [["Jan", 100.5], ["Feb", 200.25]],
                "totals": ["Betrag"],
            }]}}))
        self.assertEqual(out["status"], "written")
        wb = openpyxl.load_workbook(out["path"])
        ws = wb["Umsatz"]
        self.assertEqual(ws["A1"].value, "Monat")
        self.assertTrue(ws["A1"].font.bold)
        self.assertEqual(ws["A1"].fill.fill_type, "solid")
        self.assertEqual(ws.freeze_panes, "A2")
        self.assertIn("€", ws["B2"].number_format)
        self.assertEqual(ws["B4"].value, "=SUM(B2:B3)")
        self.assertGreaterEqual(
            ws.column_dimensions["A"].width, 8)

    def test_source_moves_data_server_side(self):
        out = json.loads(xlsx_tools.tool_xlsx_create({
            "path": "kombiniert.xlsx",
            "spec": {"sheets": [{
                "name": "Alle",
                "source": {"file": self.orders_path,
                           "sql": ("SELECT o.marktordernummer, o.name, a.kurs "
                                   "FROM orders o JOIN ausfuehrungen a ON "
                                   "a.marktordernummer = o.marktordernummer")},
            }]}}))
        self.assertEqual(out["sheets"][0]["rows"], 3)
        wb = openpyxl.load_workbook(out["path"])
        ws = wb["Alle"]
        self.assertEqual(ws["A1"].value, "marktordernummer")
        self.assertEqual(ws.max_row, 4)  # header + 3

    def test_master_detail_layout(self):
        out = json.loads(xlsx_tools.tool_xlsx_create({
            "path": "md.xlsx",
            "spec": {"sheets": [{
                "name": "MD",
                "master_detail": {
                    "key": "MARKTORDERNUMMER",
                    "master": {"source": {"file": self.orders_path,
                                          "sheet": "Orders"}},
                    "detail": {"source": {"file": self.orders_path,
                                          "sheet": "Ausfuehrungen"}},
                },
            }]}}))
        wb = openpyxl.load_workbook(out["path"])
        ws = wb["MD"]
        # combined header: 4 master cols + 3 detail cols (key deduped)
        self.assertEqual(ws.max_column, 7)
        # row 2 = MO-1 master (tinted, bold key), rows 3-4 its details
        self.assertEqual(ws["A2"].value, "MO-1")
        self.assertTrue(ws["A2"].font.bold)
        self.assertEqual(ws["A3"].value, None)          # detail row: master empty
        self.assertEqual(ws["E3"].value, "2026-01-02")  # first detail col
        self.assertEqual(ws.row_dimensions[3].outline_level, 1)
        # MO-2 master follows its details
        self.assertEqual(ws["A5"].value, "MO-2")
        self.assertEqual(ws["E6"].value, "2026-01-04")

    def test_inline_cap_steers_to_source(self):
        big = [["x"] * 10] * 600  # 6000 cells > cap
        out = json.loads(xlsx_tools.tool_xlsx_create({
            "path": "big.xlsx",
            "spec": {"sheets": [{"name": "S", "rows": big}]}}))
        self.assertIn("error", out)
        self.assertIn("source", out["error"])

    def test_charts_and_conditional(self):
        out = json.loads(xlsx_tools.tool_xlsx_create({
            "path": "chart.xlsx",
            "spec": {"sheets": [{
                "name": "KPI",
                "columns": [{"name": "Monat"}, {"name": "Wert"}],
                "rows": [["Jan", 5], ["Feb", -3], ["Mrz", 8]],
                "charts": [{"type": "bar", "labels": "Monat",
                            "series": ["Wert"], "title": "Verlauf"}],
                "conditional": [{"columns": ["Wert"],
                                 "rule": {"lt": 0, "fill": "red"}}],
            }]}}))
        wb = openpyxl.load_workbook(out["path"])
        ws = wb["KPI"]
        self.assertEqual(len(ws._charts), 1)
        self.assertEqual(ws._charts[0].title.tx.rich.p[0].r[0].t, "Verlauf")
        ranges = list(ws.conditional_formatting)
        self.assertEqual(len(ranges), 1)
        self.assertIn("B2:B4", str(ranges[0].sqref))

    def test_create_inspect_query_roundtrip(self):
        created = json.loads(xlsx_tools.tool_xlsx_create({
            "path": "loop.xlsx",
            "spec": {"sheets": [{
                "name": "Daten",
                "columns": [{"name": "K"}, {"name": "V", "format": "int"}],
                "rows": [["a", 1], ["b", 2]],
            }]}}))
        rep = json.loads(xlsx_tools.tool_xlsx_inspect(
            {"path": created["path"]}))["report"]
        self.assertIn("Sheet: Daten", rep)
        q = json.loads(xlsx_tools.tool_xlsx_query({
            "path": created["path"],
            "sql": "SELECT SUM(v) FROM daten"}))
        self.assertIn("| 3 |", q["result"])


class TestWriteDocumentXlsxRouting(_ToolFixture):
    """write_document .xlsx keeps its markdown contract (## sections → sheets,
    pipe tables, numeric coercion) but now renders through xlsx_tools —
    headers styled, freeze panes, no data change."""

    def test_two_sheet_markdown(self):
        content = (
            "## Umsatz\n\n"
            "| Monat | Betrag |\n|---|---|\n| Jan | 10.5 |\n| Feb | 20 |\n\n"
            "## Notizen\n\nKein Tabelleninhalt hier.\n")
        out = json.loads(brain.tool_write_document(
            {"path": "wd.xlsx", "content": content}))
        wb = openpyxl.load_workbook(out["path"])
        self.assertEqual(wb.sheetnames, ["Umsatz", "Notizen"])
        ws = wb["Umsatz"]
        self.assertEqual(ws["A1"].value, "Monat")
        self.assertEqual(ws["B2"].value, 10.5)   # float coercion intact
        self.assertEqual(ws["B3"].value, 20)     # int coercion intact
        self.assertTrue(ws["A1"].font.bold)      # NEW: styled header
        self.assertEqual(ws.freeze_panes, "A2")

    def test_single_table_no_sections(self):
        out = json.loads(brain.tool_write_document(
            {"path": "one.xlsx",
             "content": "| A | B |\n|---|---|\n| 1 | 2 |\n"}))
        wb = openpyxl.load_workbook(out["path"])
        self.assertEqual(wb.sheetnames, ["Sheet1"])
        self.assertEqual(wb["Sheet1"]["A2"].value, 1)


class TestXlsxEdit(_ToolFixture):
    def _create_base(self):
        out = json.loads(xlsx_tools.tool_xlsx_create({
            "path": "base.xlsx",
            "spec": {"sheets": [{
                "name": "T",
                "columns": [{"name": "Name"}, {"name": "Stueck"},
                            {"name": "Kurs"}],
                "rows": [["Alice", 10, 2.5], ["Bob", 20, 3.0]],
            }]}}))
        return out["path"]

    def test_append_rows_inherits_style(self):
        path = self._create_base()
        out = json.loads(xlsx_tools.tool_xlsx_edit({
            "path": path,
            "spec": {"ops": [{"op": "append_rows", "sheet": "T",
                              "rows": [["Carol", 30, 4.0]]}]}}))
        self.assertEqual(out["ops_applied"][0]["rows_affected"], 1)
        wb = openpyxl.load_workbook(path)
        ws = wb["T"]
        self.assertEqual(ws["A4"].value, "Carol")
        # style inherited from the last data row (border applied by create)
        self.assertEqual(ws["A4"].border.left.style, "thin")
        # untouched cells keep their formatting
        self.assertTrue(ws["A1"].font.bold)

    def test_add_column_formula_fills_rows(self):
        path = self._create_base()
        out = json.loads(xlsx_tools.tool_xlsx_edit({
            "path": path,
            "spec": {"ops": [{"op": "add_column", "sheet": "T",
                              "name": "Wert", "formula": "=B{row}*C{row}",
                              "format": "number"}]}}))
        self.assertEqual(out["ops_applied"][0]["rows_affected"], 2)
        wb = openpyxl.load_workbook(path)
        ws = wb["T"]
        self.assertEqual(ws["D1"].value, "Wert")
        self.assertEqual(ws["D2"].value, "=B2*C2")
        self.assertEqual(ws["D3"].value, "=B3*C3")

    def test_update_cells_where(self):
        path = self._create_base()
        out = json.loads(xlsx_tools.tool_xlsx_edit({
            "path": path,
            "spec": {"ops": [{"op": "update_cells", "sheet": "T",
                              "where": {"column": "Name", "equals": "Bob"},
                              "set": {"Stueck": 99}}]}}))
        self.assertEqual(out["ops_applied"][0]["rows_affected"], 1)
        wb = openpyxl.load_workbook(path)
        ws = wb["T"]
        self.assertEqual(ws["B3"].value, 99)
        self.assertEqual(ws["B2"].value, 10)  # non-matching row untouched

    def test_sheet_ops(self):
        path = self._create_base()
        out = json.loads(xlsx_tools.tool_xlsx_edit({
            "path": path,
            "spec": {"ops": [
                {"op": "add_sheet", "name": "Neu",
                 "rows": [["A", "B"], [1, 2]]},
                {"op": "rename_sheet", "from": "T", "to": "Alt"},
            ]}}))
        self.assertEqual(len(out["ops_applied"]), 2)
        wb = openpyxl.load_workbook(path)
        self.assertEqual(set(wb.sheetnames), {"Alt", "Neu"})
        out2 = json.loads(xlsx_tools.tool_xlsx_edit({
            "path": path,
            "spec": {"ops": [{"op": "delete_sheet", "name": "Neu"}]}}))
        self.assertEqual(out2["status"], "edited")
        wb2 = openpyxl.load_workbook(path)
        self.assertEqual(wb2.sheetnames, ["Alt"])

    def test_unknown_sheet_is_error(self):
        path = self._create_base()
        out = json.loads(xlsx_tools.tool_xlsx_edit({
            "path": path,
            "spec": {"ops": [{"op": "append_rows", "sheet": "Nope",
                              "rows": [[1]]}]}}))
        self.assertIn("error", out)
        self.assertIn("not found", out["error"])


if __name__ == "__main__":
    unittest.main()
