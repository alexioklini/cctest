"""Encrypted-persistence tests for the pseudonymizer.

Uses a temporary chats.db + temporary keyfile so the real Brain agent's
on-disk state is never touched.

Run with: python3 -m unittest tests.test_pseudonymizer_persistence -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pseudonymizer as ps  # noqa: E402


class _PersistenceFixture(unittest.TestCase):
    """Base class — sets up a sandbox chats.db + keyfile for each test."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="brain-ps-test-")
        # Sandbox the keyfile.
        self.key_path = os.path.join(self.tmpdir, "pseudonym.key")
        ps._KEY_PATH_OVERRIDE = self.key_path
        ps._KEY_CACHE = None  # force re-read so override takes effect

        # Sandbox the chats.db. ChatDB uses module-level CHAT_DB; override it
        # and clear any cached connection in this thread.
        import server_lib.db as _dbmod
        self._dbmod = _dbmod
        self._orig_chat_db = _dbmod.CHAT_DB
        self.chat_db_path = os.path.join(self.tmpdir, "chats.db")
        _dbmod.CHAT_DB = self.chat_db_path
        # Reset thread-local connection pool so the new path takes effect.
        try:
            _dbmod._db_pool.conns = {}
        except AttributeError:
            pass

        # Minimal session row so DELETE cascade tests work. We don't need the
        # full ChatDB.init() schema for most tests, but it doesn't hurt — and
        # it creates the pseudonym_maps table we need.
        _dbmod.ChatDB.init()

    def tearDown(self):
        import server_lib.db as _dbmod
        # Close the per-thread sqlite connection so the file can be removed.
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


class TestKeyBootstrap(_PersistenceFixture):

    def test_key_created_on_first_use(self):
        self.assertFalse(os.path.exists(self.key_path))
        key = ps._load_or_create_key()
        self.assertTrue(os.path.exists(self.key_path))
        self.assertEqual(len(key), 32)

    def test_key_is_stable(self):
        k1 = ps._load_or_create_key()
        # Force the cache to drop so we re-read from disk.
        ps._KEY_CACHE = None
        k2 = ps._load_or_create_key()
        self.assertEqual(k1, k2)

    def test_key_permissions_locked_down(self):
        ps._load_or_create_key()
        st = os.stat(self.key_path)
        # On POSIX, expect mode 0600. On Windows the chmod is best-effort, so
        # only check on systems where it's enforced.
        if hasattr(os, "geteuid"):  # POSIX
            self.assertEqual(st.st_mode & 0o777, 0o600,
                             f"keyfile permissions {oct(st.st_mode & 0o777)} != 0o600")

    def test_corrupt_keyfile_raises(self):
        # Write a wrong-length keyfile.
        os.makedirs(os.path.dirname(self.key_path), exist_ok=True)
        with open(self.key_path, "wb") as f:
            f.write(b"too-short")
        ps._KEY_CACHE = None
        with self.assertRaises(RuntimeError):
            ps._load_or_create_key()


class TestEncryptionRoundtrip(_PersistenceFixture):

    def test_encrypt_then_decrypt_restores_full_mapping(self):
        m = ps.new_mapping()
        m.forward["alice@example.com"] = "<EMAIL_1_xyz>"
        m.reverse["<EMAIL_1_xyz>"] = "alice@example.com"
        m.counters["EMAIL"] = 1
        m.sources.append("chat_text")
        m.finding_counts["email"] = 1

        nonce, ct = ps.encrypt_mapping(m)
        self.assertEqual(len(nonce), 12)
        self.assertGreater(len(ct), 0)
        self.assertNotIn(b"alice", ct, "ciphertext should not leak plaintext")

        restored = ps.decrypt_mapping(m.mapping_id, nonce, ct)
        self.assertEqual(restored.mapping_id, m.mapping_id)
        self.assertEqual(restored.salt, m.salt)
        self.assertEqual(restored.forward, m.forward)
        self.assertEqual(restored.reverse, m.reverse)
        self.assertEqual(restored.counters, m.counters)
        self.assertEqual(restored.sources, m.sources)
        self.assertEqual(restored.finding_counts, m.finding_counts)
        ps.close_mapping(m.mapping_id)

    def test_aad_binding_detects_id_swap(self):
        """Encrypting with mapping_id A then decrypting with mapping_id B
        must fail — the AAD binds the ciphertext to its identity."""
        from cryptography.exceptions import InvalidTag
        m = ps.new_mapping()
        m.forward["x"] = "<Y_1_z>"
        nonce, ct = ps.encrypt_mapping(m)
        with self.assertRaises(InvalidTag):
            ps.decrypt_mapping("attacker-supplied-different-id", nonce, ct)
        ps.close_mapping(m.mapping_id)

    def test_tampered_ciphertext_rejected(self):
        from cryptography.exceptions import InvalidTag
        m = ps.new_mapping()
        m.forward["x"] = "<Y_1_z>"
        nonce, ct = ps.encrypt_mapping(m)
        tampered = bytearray(ct)
        tampered[0] ^= 0x01
        with self.assertRaises(InvalidTag):
            ps.decrypt_mapping(m.mapping_id, nonce, bytes(tampered))
        ps.close_mapping(m.mapping_id)


class TestSavedMappingSurvivesReload(_PersistenceFixture):
    """Save → drop in-memory → load → deanonymize must still work."""

    def test_save_load_then_deanonymize(self):
        from server_lib.db import ChatDB

        # Insert a sham session row so the orphan-purge query doesn't kill us.
        with self._dbmod._db_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_id, created_at) "
                "VALUES (?, ?, strftime('%s','now'))",
                ("test-session", "main"))
            conn.commit()

        m = ps.new_mapping()
        # Use a fake span for a deterministic test.
        text = "User alice@example.com sent the message."
        findings = [{
            "rule_id": "email", "label": "Email",
            "start": 5, "end": 22, "len": 17,
            "category": "contact", "action": "warn",
        }]
        anonymised = ps.pseudonymize_text(text, findings, mapping=m)
        self.assertNotIn("alice@example.com", anonymised)

        ps.save_mapping(m, session_id="test-session", turn_id="t1")
        mid = m.mapping_id

        # Simulate Brain restart: drop in-memory, leave only the encrypted row.
        ps.close_mapping(mid)
        self.assertIsNone(ps.get_mapping(mid))

        # Reload via the persistence API.
        loaded = ps.load_mapping(mid)
        self.assertIsNotNone(loaded)
        ps.restore_mapping_to_registry(loaded)

        # Now deanonymize against the loaded mapping.
        restored, n = ps.deanonymize_text(anonymised, mapping=loaded)
        self.assertEqual(restored, text)
        self.assertEqual(n, 1)

    def test_load_missing_returns_none(self):
        self.assertIsNone(ps.load_mapping("nonexistent-mapping-id"))

    def test_save_is_idempotent(self):
        from server_lib.db import ChatDB

        with self._dbmod._db_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_id, created_at) "
                "VALUES (?, ?, strftime('%s','now'))",
                ("test-session", "main"))
            conn.commit()

        m = ps.new_mapping()
        m.forward["a"] = "<B_1_z>"
        m.reverse["<B_1_z>"] = "a"

        ps.save_mapping(m, session_id="test-session")
        ps.save_mapping(m, session_id="test-session")  # second save → overwrite

        rows = ChatDB.list_pseudonym_maps_for_session("test-session")
        self.assertEqual(len(rows), 1, "should not create a duplicate row")
        ps.close_mapping(m.mapping_id)


class TestDeleteCascade(_PersistenceFixture):
    """delete_session must drop pseudonym_maps rows for that session."""

    def test_delete_session_cascades(self):
        from server_lib.db import ChatDB

        with self._dbmod._db_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_id, created_at) "
                "VALUES (?, ?, strftime('%s','now'))",
                ("s-cascade", "main"))
            conn.commit()

        m = ps.new_mapping()
        m.forward["a"] = "<B_1_z>"
        ps.save_mapping(m, session_id="s-cascade")

        # Confirm row exists.
        self.assertIsNotNone(ChatDB.load_pseudonym_map(m.mapping_id))

        ChatDB.delete_session("s-cascade")

        # Row should be gone.
        self.assertIsNone(ChatDB.load_pseudonym_map(m.mapping_id))
        ps.close_mapping(m.mapping_id)

    def test_delete_pseudonym_map_explicit(self):
        from server_lib.db import ChatDB

        with self._dbmod._db_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_id, created_at) "
                "VALUES (?, ?, strftime('%s','now'))",
                ("s-explicit", "main"))
            conn.commit()

        m = ps.new_mapping()
        m.forward["a"] = "<B_1_z>"
        ps.save_mapping(m, session_id="s-explicit")
        ps.delete_persisted_mapping(m.mapping_id)
        self.assertIsNone(ChatDB.load_pseudonym_map(m.mapping_id))
        ps.close_mapping(m.mapping_id)


class TestOrphanPurge(_PersistenceFixture):
    """purge_orphan_pseudonym_maps must drop rows whose session is gone."""

    def test_purges_session_orphans(self):
        from server_lib.db import ChatDB

        # Map with no corresponding sessions row → orphan from a botched
        # delete somewhere upstream.
        m = ps.new_mapping()
        m.forward["a"] = "<B_1_z>"
        ps.save_mapping(m, session_id="ghost-session")

        n = ChatDB.purge_orphan_pseudonym_maps()
        self.assertGreaterEqual(n, 1)
        self.assertIsNone(ChatDB.load_pseudonym_map(m.mapping_id))
        ps.close_mapping(m.mapping_id)

    def test_keeps_maps_with_valid_session(self):
        from server_lib.db import ChatDB

        with self._dbmod._db_conn() as conn:
            conn.execute(
                "INSERT INTO sessions (id, agent_id, created_at) "
                "VALUES (?, ?, strftime('%s','now'))",
                ("s-keep", "main"))
            conn.commit()

        m = ps.new_mapping()
        m.forward["a"] = "<B_1_z>"
        ps.save_mapping(m, session_id="s-keep")

        ChatDB.purge_orphan_pseudonym_maps()
        self.assertIsNotNone(ChatDB.load_pseudonym_map(m.mapping_id))
        ps.close_mapping(m.mapping_id)


if __name__ == "__main__":
    unittest.main()
