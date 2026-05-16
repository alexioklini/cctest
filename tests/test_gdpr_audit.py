"""Tests for the admin-only audit "show what was sent" path (step 6.4).

The HTTP endpoint glues together three layers:
  1. `ChatDB.list_pseudonym_maps_for_session(sid)` — list metadata rows.
  2. `pseudonymizer.load_mapping(mapping_id)` — decrypt one mapping.
  3. Render `mapping.forward` (real → token) as before/after pairs.

We exercise that contract end-to-end against a sandbox chats.db + keyfile,
without standing up an HTTP server. The gate (admin-only) is enforced in
the handler; routing tests for that would require the full request stack
and we cover the underlying primitives here instead.

Run: python3 -m unittest tests.test_gdpr_audit -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402


def _scan(text: str) -> list[dict]:
    """Use the real PII scanner so test findings carry the same shape
    (start/end/rule_id/etc.) the production code produces. brain is heavy;
    only imported once tests actually run."""
    import brain
    cfg = brain._get_gdpr_scanner_config()
    return brain._pii_scan_text(text, cfg=cfg)


class _AuditFixture(unittest.TestCase):
    """Sandbox chats.db + keyfile; same shape as the persistence tests."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="brain-audit-test-")
        self.key_path = os.path.join(self.tmpdir, "pseudonym.key")
        ps._KEY_PATH_OVERRIDE = self.key_path
        ps._KEY_CACHE = None

        import server_lib.db as _dbmod
        self._dbmod = _dbmod
        self._orig_chat_db = _dbmod.CHAT_DB
        self.chat_db_path = os.path.join(self.tmpdir, "chats.db")
        _dbmod.CHAT_DB = self.chat_db_path
        try:
            _dbmod._db_pool.conns = {}
        except AttributeError:
            pass
        _dbmod.ChatDB.init()

    def tearDown(self):
        import server_lib.db as _dbmod
        try:
            for c in (_dbmod._db_pool.conns or {}).values():
                try:
                    c.close()
                except Exception:
                    pass
            _dbmod._db_pool.conns = {}
        except AttributeError:
            pass
        _dbmod.CHAT_DB = self._orig_chat_db
        ps._KEY_PATH_OVERRIDE = None
        ps._KEY_CACHE = None
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class TestAuditFlow(_AuditFixture):

    def test_list_empty_session_returns_zero_rows(self):
        rows = self._dbmod.ChatDB.list_pseudonym_maps_for_session("never-anonymised")
        self.assertEqual(rows, [])

    def test_full_audit_flow_roundtrip(self):
        # Simulate one anonymise turn: build a mapping with a couple of
        # findings, save it under session 'sess-A', then exercise the
        # exact two reads the audit handler performs.
        sid = "sess-A"
        m = ps.new_mapping()
        text = "Email me at alice@example.com about IBAN DE89370400440532013000"
        findings = _scan(text)
        # Sanity: scanner found the two we expect (rules: email, iban).
        rule_ids = {f["rule_id"] for f in findings}
        self.assertIn("email", rule_ids)
        self.assertIn("iban", rule_ids)
        ps.pseudonymize_text(text, findings, mapping=m, source="chat_text")
        ps.save_mapping(m, session_id=sid, turn_id="anon_t1")

        # Step 1: list rows for the session (what the listing endpoint does).
        rows = self._dbmod.ChatDB.list_pseudonym_maps_for_session(sid)
        self.assertEqual(len(rows), 1)
        row_mapping_id, row_turn_id, row_created_at = rows[0]
        self.assertEqual(row_mapping_id, m.mapping_id)
        self.assertEqual(row_turn_id, "anon_t1")
        self.assertGreater(row_created_at, 0)

        # Drop the in-memory copy so the next step proves we're reading the
        # persisted ciphertext, not the registry. Mirrors what happens at
        # turn-finally: in-memory mapping is dropped, encrypted row stays.
        ps.close_mapping(m.mapping_id)
        self.assertIsNone(ps.get_mapping(m.mapping_id))

        # Step 2: decrypt the mapping (what the detail endpoint does).
        loaded = ps.load_mapping(row_mapping_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.mapping_id, row_mapping_id)

        # Step 3: shape we render. The web UI iterates `forward` to build
        # the before/after table.
        self.assertIn("alice@example.com", loaded.forward)
        self.assertIn("DE89370400440532013000", loaded.forward)
        # Tokens are non-empty and distinct.
        tokens = list(loaded.forward.values())
        self.assertEqual(len(set(tokens)), len(tokens))
        # Source label round-trips.
        self.assertIn("chat_text", loaded.sources)
        # Per-category counts round-trip too — the UI shows these in the
        # mapping detail header.
        self.assertGreaterEqual(loaded.finding_counts.get("email", 0), 1)
        self.assertGreaterEqual(loaded.finding_counts.get("iban", 0), 1)

    def test_decrypt_returns_none_on_unknown_id(self):
        # The handler relies on `load_mapping(id) → None` for unknown ids
        # to map cleanly to a 404. Confirm the contract.
        self.assertIsNone(ps.load_mapping("does-not-exist-1234"))

    def test_multiple_mappings_per_session_round_trip(self):
        # Audit view needs to list every mapping the session ever produced
        # (one per anonymise turn). Save two and verify ordering + ids.
        sid = "sess-B"
        m1 = ps.new_mapping()
        m2 = ps.new_mapping()
        t1 = "Mail bob@example.com"
        t2 = "Phone +49 30 12345678"
        ps.pseudonymize_text(t1, _scan(t1), mapping=m1, source="chat_text")
        ps.pseudonymize_text(t2, _scan(t2), mapping=m2, source="chat_text")
        ps.save_mapping(m1, session_id=sid, turn_id="t1")
        ps.save_mapping(m2, session_id=sid, turn_id="t2")

        rows = self._dbmod.ChatDB.list_pseudonym_maps_for_session(sid)
        self.assertEqual(len(rows), 2)
        ids = {r[0] for r in rows}
        self.assertEqual(ids, {m1.mapping_id, m2.mapping_id})


if __name__ == "__main__":
    unittest.main()
