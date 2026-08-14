"""Regression tests for the MemPalace code-review fixes (mempalace-review).

Each class pins one review finding, at the narrowest testable seam:

  C1  `/v1/mempalace/drawers` admin gate is enforced INSIDE the handler
      (trailing-slash path-table bypass no longer reaches the palace).
  H4  `/v1/mempalace/session-turns` requires session ownership.
  H1  remote reranker is skipped when an anonymise mapping is active
      (raw-drawer PII must not egress to the /rerank endpoint pre-seam).
  H2  the immediate chat-sync cursor is clamped below the first failed
      message id (v9.60.4 write-loss guard on the live path).
  M3  kg_search project scoping uses exact case-sensitive prefix matching
      (no LIKE wildcard / case sibling-project leak).
  M4  GDPR retrieval seams fail CLOSED on scanner/sweep crashes.
  M6  the miner venv-patch script applies/reverts idempotently and refuses
      a drift (mempalace version change) instead of corrupting.
  M7  KG result lists are capped by serialized size.
  M10 code-read mempalace config keys exist in config.example.json.

Run: python3 -m unittest tests.test_mempalace_review_fixes -v
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import types
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
import pseudonymizer as _ps_mod  # noqa: E402
from engine import mempalace_glue  # noqa: E402
from engine.context import get_request_context, request_context  # noqa: E402
from server_lib import mempalace_sync  # noqa: E402
from handlers import admin_observability as _obs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _FakeHandler(_obs.AdminObservabilityHandlers):
    """Minimal handler shell: stubs the auth helpers, records responses."""

    def __init__(self, path, auth_user, *, session_check_result=None):
        self.path = path
        self._auth_user = auth_user
        self.responses = []
        self._session_check_result = session_check_result
        self._checked_sid = None

    def _send_json(self, payload, code=200):
        self.responses.append((code, payload))

    def _require_auth(self):
        return self._auth_user

    def _require_role(self, *roles):
        if self._auth_user["role"] in roles:
            return self._auth_user
        self._send_json({"error": "Insufficient permissions"}, 403)
        return None

    def _session_access_check(self, sid, *, require_manage=False):
        self._checked_sid = sid
        if self._session_check_result is None:
            # Mirror the real helper: it sends the 403 itself and returns None.
            self._send_json({"error": "Access to this session is not permitted"}, 403)
        return self._session_check_result


class _FakeMapping:
    """Minimal stand-in for a pseudonymizer mapping."""
    mapping_id = "test-mapping"
    forward: dict = {}
    reverse: dict = {}
    categories: dict = {}


class _PalaceNotFoundConfig:
    """engine._load_mempalace_config stand-in: palace_path is not a dir, so
    the handler short-circuits at the 404 instead of touching the palace."""
    @staticmethod
    def _load_mempalace_config():
        return {"palace_path": "/nonexistent/palace-for-test"}


# ─── C1: drawers handler admin gate ───────────────────────────────────────────

class TestDrawersAdminGate(unittest.TestCase):
    """C1 — GET /v1/mempalace/drawers/ (trailing slash) previously bypassed the
    server's exact-path admin table; the handler must refuse non-admins itself."""

    def setUp(self):
        self._saved_engine = getattr(_obs, "engine", None)
        _obs.engine = _PalaceNotFoundConfig  # injected-global stand-in

    def tearDown(self):
        if self._saved_engine is None:
            _obs.engine = None
        else:
            _obs.engine = self._saved_engine

    def test_non_admin_is_refused_before_any_palace_access(self):
        h = _FakeHandler(
            "/v1/mempalace/drawers/?wing=user__victim", {"role": "user", "id": "u1"})
        h._handle_mempalace_drawers()  # must NOT raise / reach the palace
        self.assertEqual(len(h.responses), 1)
        self.assertEqual(h.responses[0][0], 403)

    def test_admin_reaches_the_palace_check(self):
        h = _FakeHandler(
            "/v1/mempalace/drawers/?wing=user__x", {"role": "admin", "id": "a1"})
        h._handle_mempalace_drawers()
        # Passed the gate → hit the (missing) palace → 404, not 403.
        self.assertEqual(h.responses[0][0], 404)


# ─── H4: session-turns ownership ──────────────────────────────────────────────

class TestSessionTurnsOwnership(unittest.TestCase):
    """H4 — the endpoint scans the whole palace for a session's drawer
    prefixes; a foreign user must be refused before the scan."""

    def setUp(self):
        self._saved_engine = getattr(_obs, "engine", None)
        _obs.engine = _PalaceNotFoundConfig

    def tearDown(self):
        if self._saved_engine is None:
            _obs.engine = None
        else:
            _obs.engine = self._saved_engine

    def test_denied_user_gets_403_and_no_palace_scan(self):
        h = _FakeHandler("/v1/mempalace/session-turns?session_id=s1",
                         {"role": "user", "id": "u1"}, session_check_result=None)
        h._handle_mempalace_session_turns()
        self.assertEqual(h._checked_sid, "s1")
        self.assertEqual(h.responses[0][0], 403)

    def test_owner_proceeds_to_empty_turn_list(self):
        h = _FakeHandler("/v1/mempalace/session-turns?session_id=s1",
                         {"role": "user", "id": "u1"}, session_check_result={"id": "s1"})
        h._handle_mempalace_session_turns()
        self.assertEqual(h.responses[0][0], 200)
        self.assertEqual(h.responses[0][1]["session_id"], "s1")
        self.assertEqual(h.responses[0][1]["turn_ids"], [])

    def test_missing_session_id_400(self):
        h = _FakeHandler("/v1/mempalace/session-turns",
                         {"role": "user", "id": "u1"}, session_check_result={"id": "s1"})
        h._handle_mempalace_session_turns()
        self.assertEqual(h.responses[0][0], 400)


# ─── H1: remote-reranker PII egress gate ──────────────────────────────────────

class TestRerankerEgressGate(unittest.TestCase):
    """H1 — with an anonymise mapping active, a device=remote reranker must be
    skipped (it would POST raw drawer text pre-seam); local stays allowed."""

    REMOTE = {"reranker": {"enabled": True, "device": "remote"}}
    LOCAL = {"reranker": {"enabled": True, "device": "auto"}}
    OFF = {"reranker": {"enabled": False, "device": "remote"}}

    def test_remote_blocked_when_mapping_active(self):
        with request_context(_gdpr_mapping_id="m1"):
            self.assertTrue(mempalace_glue._reranker_egress_blocked(self.REMOTE))

    def test_remote_allowed_without_mapping(self):
        with request_context(_gdpr_mapping_id=""):
            self.assertFalse(mempalace_glue._reranker_egress_blocked(self.REMOTE))

    def test_local_reranker_never_blocked(self):
        with request_context(_gdpr_mapping_id="m1"):
            self.assertFalse(mempalace_glue._reranker_egress_blocked(self.LOCAL))

    def test_disabled_reranker_never_blocked(self):
        with request_context(_gdpr_mapping_id="m1"):
            self.assertFalse(mempalace_glue._reranker_egress_blocked(self.OFF))


# ─── H2: cursor write-loss clamp ──────────────────────────────────────────────

class TestCursorClamp(unittest.TestCase):
    """H2 — the immediate-sync cursor must never advance past a failed write."""

    def test_no_failure_advances_to_max(self):
        self.assertEqual(mempalace_sync.mempalace_cursor_clamp(42, None), 42)
        self.assertEqual(mempalace_sync.mempalace_cursor_clamp(42, 0), 42)

    def test_failure_clamps_below_lowest_failed(self):
        self.assertEqual(mempalace_sync.mempalace_cursor_clamp(42, 7), 6)
        self.assertEqual(mempalace_sync.mempalace_cursor_clamp(42, 1), 0)

    def test_lowest_failed_beyond_max_is_noop(self):
        # lowest_failed > max_id can't happen (failures are message ids) but
        # the clamp must stay safe: never negative, never above max_id.
        self.assertEqual(mempalace_sync.mempalace_cursor_clamp(5, 99), 5)


# ─── M3: kg_search exact-prefix scoping ───────────────────────────────────────

class TestKgScopeSql(unittest.TestCase):
    """M3 — `source_file LIKE prefix || '%'` leaked triples across sibling
    dirs (`_` wildcard) and cases; `instr(source_file, ?) = 1` must not."""

    def _run_scope(self, prefixes):
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                "CREATE TABLE triples (subject TEXT, source_file TEXT, valid_to TEXT)")
            rows = [
                ("own", "/data/my_project/doc.md", None),
                ("own_nested", "/data/my_project/sub/doc.md", None),
                ("sibling_underscore", "/data/myXproject/doc.md", None),
                ("sibling_prefix", "/data/my_project2/doc.md", None),
                ("case", "/data/PROJECT/doc.md", None),
                ("other", "/data/other_project/doc.md", None),
            ]
            conn.executemany(
                "INSERT INTO triples (subject, source_file, valid_to) VALUES (?,?,?)", rows)
            clause, params = mempalace_glue._kg_scope_sql(prefixes)
            out = conn.execute(
                f"SELECT subject FROM triples WHERE ({clause}) AND valid_to IS NULL",
                params).fetchall()
            return {r[0] for r in out}
        finally:
            conn.close()

    def test_only_exact_prefix_matches(self):
        got = self._run_scope(["/data/my_project/"])
        self.assertEqual(got, {"own", "own_nested"})

    def test_multi_prefix_union(self):
        got = self._run_scope(["/data/my_project/", "/data/other_project/"])
        self.assertEqual(got, {"own", "own_nested", "other"})


# ─── M4: GDPR retrieval seams fail closed ─────────────────────────────────────

class TestGdprRetrievalFailClosed(unittest.TestCase):
    """M4 — a scanner/sweep crash in an anonymising session must withhold
    retrieval content, never ship the raw original."""

    def _ctx(self, mapping_id="m1"):
        return request_context(_gdpr_mapping_id=mapping_id)

    def test_retrieval_guard_refuses_on_scan_error(self):
        with self._ctx():
            with unittest.mock.patch.object(
                    brain, "_gdpr_retrieval_new_values",
                    side_effect=RuntimeError("scan boom")):
                refusal = brain._gdpr_retrieval_guard(
                    "raw PII text", "mempalace_query", _FakeMapping())
        self.assertIsNotNone(refusal)
        payload = json.loads(refusal)
        self.assertEqual(payload["error"], "retrieval_pii_withheld")
        self.assertEqual(payload["source"], "mempalace_query")

    def test_anon_tool_text_refuses_retrieval_source_on_sweep_error(self):
        with self._ctx():
            with unittest.mock.patch.object(
                    brain, "_gdpr_retrieval_new_values", return_value={}), \
                 unittest.mock.patch.object(
                    brain, "_review_anon_override", return_value=None), \
                 unittest.mock.patch.object(
                    _ps_mod, "get_mapping", return_value=_FakeMapping()), \
                 unittest.mock.patch.object(
                    _ps_mod, "apply_entity_variants",
                    side_effect=RuntimeError("sweep boom")), \
                 unittest.mock.patch.object(
                    brain, "_classification_gate_tool_text", return_value=None):
                out = brain._gdpr_anon_tool_text(
                    "Secret Project Name", "mempalace_query", classify=False)
        self.assertEqual(json.loads(out)["error"], "retrieval_pii_withheld")

    def test_non_retrieval_source_keeps_legacy_fail_open(self):
        with self._ctx():
            with unittest.mock.patch.object(
                    brain, "_gdpr_retrieval_new_values", return_value={}), \
                 unittest.mock.patch.object(
                    brain, "_review_anon_override", return_value=None), \
                 unittest.mock.patch.object(
                    _ps_mod, "get_mapping", return_value=_FakeMapping()), \
                 unittest.mock.patch.object(
                    _ps_mod, "apply_entity_variants",
                    side_effect=RuntimeError("sweep boom")), \
                 unittest.mock.patch.object(
                    _ps_mod, "apply_known_values",
                    return_value=("x", 0)):
                out = brain._gdpr_anon_tool_text(
                    "document body", "read_document", classify=False)
        # Non-retrieval read tools keep returning the original on crash
        # (documented legacy stance) — this must NOT be a refusal JSON.
        self.assertEqual(out, "document body")


# ─── M6: miner venv-patch script ─────────────────────────────────────────────

class TestMinerPatchScript(unittest.TestCase):
    """M6 — the patch script must apply idempotently, revert cleanly, and
    refuse a drifted (mempalace-upgraded) file instead of corrupting it."""

    def _load_script(self):
        import importlib.util
        path = os.path.join(ROOT, "scripts", "patch_mempalace_miner.py")
        spec = importlib.util.spec_from_file_location("pmmp", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_apply_revert_roundtrip(self):
        mod = self._load_script()
        patched = mod.apply_patch(mod.OLD)
        self.assertIn(mod.MARKER, patched)
        self.assertNotIn(mod.OLD, patched)
        self.assertEqual(mod.revert_patch(patched), mod.OLD)

    def test_apply_is_idempotent(self):
        mod = self._load_script()
        patched = mod.apply_patch(mod.OLD)
        self.assertEqual(mod.apply_patch(patched), patched)

    def test_refuses_drifted_anchor(self):
        mod = self._load_script()
        drifted = mod.OLD.replace("assert_no_collisions", "assert_no_collisions_x")
        with self.assertRaises(ValueError):
            mod.apply_patch(drifted)


# ─── M7: KG result size cap ───────────────────────────────────────────────────

class TestKgResultCap(unittest.TestCase):
    """M7 — _cap_result_list truncates once the serialized JSON exceeds the
    budget, keeping the head (highest-relevance) rows."""

    def test_truncates_over_budget(self):
        items = [{"span": "x" * 5000} for _ in range(20)]  # ~100KB raw
        capped = mempalace_glue._cap_result_list(items, budget=30_000)
        self.assertGreater(len(capped), 0)
        self.assertLess(len(capped), len(items))
        size = len(json.dumps(capped, ensure_ascii=False))
        self.assertLessEqual(size, 30_000 + 5000)  # at most one item over

    def test_small_lists_untouched(self):
        items = [{"span": "short"} for _ in range(5)]
        self.assertEqual(mempalace_glue._cap_result_list(items), items)


# ─── M10: config.example.json key coverage ───────────────────────────────────

class TestMempalaceConfigKeys(unittest.TestCase):
    """M10 — keys the code reads must exist in config.example.json so a fresh
    install doesn't silently run on defaults the UI can't configure."""

    def setUp(self):
        with open(os.path.join(ROOT, "config.example.json")) as f:
            self.cfg = json.load(f)
        self.mp = self.cfg.get("mempalace") or {}

    def test_reranker_remote_keys_present(self):
        rr = self.mp.get("reranker") or {}
        self.assertIn("remote_timeout_s", rr)
        self.assertIn("api_key", rr)

    def test_kg_parallel_workers_present_and_batch_size_gone(self):
        kg = self.mp.get("kg") or {}
        self.assertIn("parallel_workers", kg)
        self.assertNotIn("batch_size", kg)  # dead key — no reader

    def test_chat_sync_block_still_present(self):
        # The block still carries the legacy keys + the live default_mode.
        cs = self.mp.get("chat_sync") or {}
        self.assertIn("default_mode", (cs.get("classifier") or {}))


if __name__ == "__main__":
    unittest.main()
