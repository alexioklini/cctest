"""Characterization (behavior-pinning) tests for the tool-execution layer.

These pin the PURE, deterministic behavior of the tool-exec helpers that
currently live in `brain.py` (scattered: ~2841, ~3064, ~16657-16850,
~17836-18070) BEFORE they are extracted to `engine/tool_exec.py` (refactor
Tier C, sub-step C2). The contract: after extraction these tests must still
pass byte-for-byte, or the move introduced a regression.

WHY this file exists: the tool-exec layer (dedup guard, result
sanitisation/compression/budget, the per-session read-path tracker) has NO
existing tests and is exercised only inside a live agentic turn (sidecar +
session + thread-locals). The import-gate and existing unittests therefore
cannot catch a regression in this layer's *logic*. The pieces that ARE pure
and deterministic are pinned here because the extraction risks a subtle
off-by-one (truncation boundary, keep_recent slice, dedup bound) or a
dropped exemption.

What is pinned here (no DB / network / LLM / sidecar):
  * _ok / _err                       — JSON envelope shape
  * _get_artifact_session_folder     — deterministic folder naming
  * _dedup_sid / _check_tool_dedup / reset_tool_dedup — session-scoped dedup
  * _sanitize_tool_result            — base64 strip + _mcp_images preservation
  * _compress_old_tool_results       — keep_recent slice + 500/200 boundary
  * _microcompact                    — compactable-tool clearing + >100 gate
  * _record_session_read_path / _read_doc_cache_session_paths — read tracker

What is NOT pinned (integration-only — gate blind spots for C2):
  * _apply_tool_result_budget        — writes oversized results to disk under
                                       AGENTS_DIR (file I/O); covered by the
                                       eval run, not this gate.
  * extract_attachment_text          — file I/O + doc_convert pipeline.
  * _gdpr_anon_tool_text             — pseudonymizer + thread-local mapping.
  These need disk fixtures or live state; their correctness post-extraction
  is verified by the eval run, not by this gate.

Run: python3 -m unittest tests.test_tool_exec_characterization -v
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import request_context, get_request_context  # noqa: E402


class _ThreadLocalFixture(unittest.TestCase):
    """Base: give every test a clean, known session-id scope and restore it.

    The dedup + read-path stores key off the request context's
    `current_session_id` (falling back to a per-thread sentinel). Pinning a
    fixed session id makes the scope deterministic; clearing the shared dicts in
    setUp/tearDown keeps tests independent (the stores are process-wide module
    globals). `request_context(...)` is entered via `enterContext` so it tears
    down automatically when the test ends."""

    def setUp(self):
        self.enterContext(request_context(current_session_id="chartest-sid"))
        # Clear process-wide stores so a prior test can't leak in.
        with brain._tool_dedup_lock:
            brain._tool_dedup.clear()
        with brain._session_read_paths_lock:
            brain._session_read_paths.clear()

    def tearDown(self):
        with brain._tool_dedup_lock:
            brain._tool_dedup.clear()
        with brain._session_read_paths_lock:
            brain._session_read_paths.clear()


class TestEnvelopes(unittest.TestCase):
    """_ok / _err are the universal tool-result envelope. INVARIANT: _ok wraps
    a dict as compact JSON round-tripping to the same dict; _err produces
    exactly {"error": <msg>} and nothing else. Every tool's caller parses
    these — the shape must not drift."""

    def test_ok_roundtrips_dict(self):
        out = brain._ok({"result": "success", "count": 42})
        self.assertEqual(json.loads(out), {"result": "success", "count": 42})

    def test_err_is_single_error_key(self):
        out = brain._err("file not found")
        self.assertEqual(json.loads(out), {"error": "file not found"})


class TestArtifactSessionFolder(_ThreadLocalFixture):
    """_get_artifact_session_folder names the per-session artifact dir.
    INVARIANT: `<YYYY-MM-DD>_<session_prefix>` — the date is today's, the
    suffix is derived from the session id. The miner classifies folders by
    this exact shape (sched- prefix vs chat), so the format is load-bearing."""

    def test_folder_starts_with_today_date(self):
        folder = brain._get_artifact_session_folder("abc123def456")
        today = datetime.date.today().isoformat()
        self.assertTrue(
            folder.startswith(today + "_"),
            f"expected '{today}_...' prefix, got {folder!r}",
        )

    def test_folder_is_deterministic(self):
        # Same session id -> same folder within a run (pure function of id+date).
        a = brain._get_artifact_session_folder("session-XYZ")
        b = brain._get_artifact_session_folder("session-XYZ")
        self.assertEqual(a, b)


class TestDedupSid(_ThreadLocalFixture):
    """_dedup_sid resolves the dedup scope. INVARIANT: session id when set,
    else a `_thread:<id>` sentinel so unrelated CLI/warmup calls don't share
    a dedup bucket."""

    def test_uses_session_id_when_set(self):
        get_request_context().current_session_id = "sid_xyz"
        self.assertEqual(brain._dedup_sid(), "sid_xyz")

    def test_falls_back_to_thread_sentinel(self):
        get_request_context().current_session_id = None
        self.assertEqual(brain._dedup_sid(), f"_thread:{threading.get_ident()}")


class TestCheckToolDedup(_ThreadLocalFixture):
    """_check_tool_dedup is the loop-breaker. INVARIANTS:
      * first call with given (name,args) -> None (allowed)
      * exact repeat -> error string (1st dupe), and a 2nd consecutive dupe
        raises TaskCancelled (hard loop abort)
      * a non-duplicate call between dupes RESETS the consecutive counter
      * exempt tools (memory_recall etc.) never dedup
      * the per-session call set is bounded to the last 50 once it passes 100
    These thresholds are exactly what stops a stuck model without falsely
    killing a legitimately-repeating tool."""

    def test_first_call_allowed_repeat_errors(self):
        self.assertIsNone(brain._check_tool_dedup("read_file", {"path": "/a"}))
        out = brain._check_tool_dedup("read_file", {"path": "/a"})
        self.assertIsNotNone(out)
        self.assertEqual(json.loads(out).get("error", "")[:9], "Duplicate")

    def test_second_consecutive_dupe_raises(self):
        brain._check_tool_dedup("read_file", {"path": "/a"})  # None
        brain._check_tool_dedup("read_file", {"path": "/a"})  # error (dupe 1)
        with self.assertRaises(brain.TaskCancelled):
            brain._check_tool_dedup("read_file", {"path": "/a"})  # dupe 2 -> abort

    def test_nonduplicate_resets_consecutive_counter(self):
        brain._check_tool_dedup("read_file", {"path": "/a"})        # None, add A
        out1 = brain._check_tool_dedup("read_file", {"path": "/a"}) # dupe 1 of A
        self.assertIsNotNone(out1)
        # A different call resets consecutive_dupes back to 0.
        self.assertIsNone(brain._check_tool_dedup("read_file", {"path": "/b"}))
        # So repeating A again is a *first* consecutive dupe -> error, NOT abort.
        out2 = brain._check_tool_dedup("read_file", {"path": "/a"})
        self.assertIsNotNone(out2)

    def test_exempt_tool_never_dedups(self):
        a = {"q": "x"}
        self.assertIsNone(brain._check_tool_dedup("memory_recall", a))
        self.assertIsNone(brain._check_tool_dedup("memory_recall", a))
        self.assertIsNone(brain._check_tool_dedup("memory_recall", a))

    def test_args_order_insensitive(self):
        # key uses json.dumps(args, sort_keys=True) -> arg order must not matter.
        self.assertIsNone(brain._check_tool_dedup("search", {"a": 1, "b": 2}))
        out = brain._check_tool_dedup("search", {"b": 2, "a": 1})
        self.assertIsNotNone(out)  # recognised as the same call

    def test_call_set_bounded_to_50(self):
        for i in range(101):
            brain._check_tool_dedup("read_file", {"path": f"/f{i}"})
        st = brain._tool_dedup["chartest-sid"]
        self.assertLessEqual(len(st["calls"]), 50)

    def test_reset_clears_session(self):
        brain._check_tool_dedup("read_file", {"path": "/a"})
        brain.reset_tool_dedup()
        # After reset the same call is treated as brand-new (allowed).
        self.assertIsNone(brain._check_tool_dedup("read_file", {"path": "/a"}))


class TestSanitizeToolResult(_ThreadLocalFixture):
    """_sanitize_tool_result keeps base64 image blobs out of context.
    INVARIANTS: a long `"data": "<base64>"` value is replaced with a
    placeholder; a result carrying `_mcp_images` keeps that key intact (the
    caller forwards those as real multimodal blocks). Short strings pass
    through untouched."""

    def test_strips_long_base64_data_field(self):
        blob = "A" * 600  # > 500 -> matches _BASE64_DATA_RE
        raw = json.dumps({"data": blob, "other": "keep"})
        out = brain._sanitize_tool_result("puppeteer", raw)
        self.assertNotIn(blob, out)
        self.assertIn("base64 image removed", out)
        self.assertIn("keep", out)

    def test_preserves_mcp_images_key(self):
        raw = json.dumps({"result": "see image", "_mcp_images": [{"b64": "Z" * 2000}]})
        out = brain._sanitize_tool_result("mcp_tool", raw)
        parsed = json.loads(out)
        self.assertIn("_mcp_images", parsed)
        self.assertEqual(parsed["_mcp_images"], [{"b64": "Z" * 2000}])

    def test_short_plain_text_unchanged(self):
        self.assertEqual(brain._sanitize_tool_result("read_file", "hello world"),
                         "hello world")


class TestCompressOldToolResults(_ThreadLocalFixture):
    """_compress_old_tool_results truncates OLD tool results to free budget.
    INVARIANTS: the most-recent `keep_recent` tool-result messages are left
    intact; older ones >500 chars are cut to `content[:200] + "\\n[...compressed...]"`;
    results <=500 chars are left alone. Works on both OpenAI (role=tool) and
    Anthropic (user/tool_result block) shapes."""

    def _openai_msgs(self, n, size):
        return [{"role": "tool", "tool_call_id": f"t{i}", "content": "X" * size}
                for i in range(n)]

    def test_keeps_recent_and_compresses_older_openai(self):
        msgs = self._openai_msgs(6, 800)  # all > 500
        brain._compress_old_tool_results(msgs, keep_recent=2)
        # last 2 untouched
        self.assertEqual(len(msgs[-1]["content"]), 800)
        self.assertEqual(len(msgs[-2]["content"]), 800)
        # older 4 compressed to 200 + marker
        self.assertTrue(msgs[0]["content"].endswith("\n[...compressed...]"))
        self.assertEqual(msgs[0]["content"], "X" * 200 + "\n[...compressed...]")

    def test_short_results_not_compressed(self):
        msgs = self._openai_msgs(6, 400)  # all <= 500
        brain._compress_old_tool_results(msgs, keep_recent=2)
        self.assertEqual(msgs[0]["content"], "X" * 400)  # unchanged

    def test_anthropic_block_shape(self):
        msgs = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"u{i}", "content": "Y" * 800}]}
            for i in range(5)
        ]
        brain._compress_old_tool_results(msgs, keep_recent=1)
        # oldest block compressed
        self.assertEqual(msgs[0]["content"][0]["content"],
                         "Y" * 200 + "\n[...compressed...]")
        # most recent untouched
        self.assertEqual(len(msgs[-1]["content"][0]["content"]), 800)


class TestMicrocompact(_ThreadLocalFixture):
    """_microcompact clears stale results for compactable tools only.
    INVARIANTS: needs the assistant tool_use/tool_call block to resolve the
    tool name; only tools in _MICROCOMPACT_TOOLS (and not in _EXEMPT) are
    cleared; the most-recent `keep_recent` are kept; content <=100 chars is
    left alone; the marker is `[Old <tool> result cleared]`. Returns
    (messages, tokens_freed) with tokens_freed = cleared_chars // 4."""

    def _anthropic_pair(self, uid, tool_name, content):
        # An assistant tool_use block + the matching user tool_result block.
        return [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": uid, "name": tool_name, "input": {}}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": uid, "content": content}]},
        ]

    def test_clears_old_compactable_keeps_recent(self):
        msgs = []
        for i in range(6):  # 6 read_file results, each > 100 chars
            msgs += self._anthropic_pair(f"u{i}", "read_file", "Z" * 500)
        out, freed = brain._microcompact(msgs, keep_recent=2)
        # 4 oldest cleared, 2 newest kept
        result_blocks = [b for m in out if m["role"] == "user"
                         for b in m["content"] if b.get("type") == "tool_result"]
        cleared = [b for b in result_blocks if b["content"] == "[Old read_file result cleared]"]
        kept = [b for b in result_blocks if b["content"] == "Z" * 500]
        self.assertEqual(len(cleared), 4)
        self.assertEqual(len(kept), 2)
        self.assertEqual(freed, 4 * (500 // 4))

    def test_exempt_tool_not_cleared(self):
        msgs = []
        for i in range(6):
            msgs += self._anthropic_pair(f"u{i}", "memory_recall", "Z" * 500)
        out, freed = brain._microcompact(msgs, keep_recent=2)
        self.assertEqual(freed, 0)  # memory_recall is exempt -> nothing cleared

    def test_small_results_skipped(self):
        msgs = []
        for i in range(6):
            msgs += self._anthropic_pair(f"u{i}", "read_file", "Z" * 80)  # <= 100
        out, freed = brain._microcompact(msgs, keep_recent=2)
        self.assertEqual(freed, 0)  # below the 100-char clearing gate

    def test_below_keep_recent_threshold_noop(self):
        msgs = []
        for i in range(3):  # only 3 compactable, keep_recent=5
            msgs += self._anthropic_pair(f"u{i}", "read_file", "Z" * 500)
        out, freed = brain._microcompact(msgs, keep_recent=5)
        self.assertEqual(freed, 0)
        self.assertIs(out, msgs)  # returns the same list, untouched


class TestSessionReadPaths(_ThreadLocalFixture):
    """The read-path tracker feeds the citation validator (which files to grep
    quotes against). INVARIANTS: records absolute paths, dedups, scopes per
    session id, and stops adding NEW paths once the 256 soft cap is reached."""

    def test_record_then_read_roundtrip_absolute(self):
        brain._record_session_read_path("~/doc.pdf")
        paths = brain._read_doc_cache_session_paths()
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0], os.path.abspath(os.path.expanduser("~/doc.pdf")))

    def test_dedups_same_path(self):
        brain._record_session_read_path("/tmp/a.txt")
        brain._record_session_read_path("/tmp/a.txt")
        self.assertEqual(brain._read_doc_cache_session_paths(), ["/tmp/a.txt"])

    def test_scoped_per_session(self):
        brain._record_session_read_path("/tmp/a.txt")
        get_request_context().current_session_id = "other-sid"
        self.assertEqual(brain._read_doc_cache_session_paths(), [])

    def test_soft_cap_stops_new_paths(self):
        for i in range(brain._SESSION_READ_PATHS_MAX + 10):
            brain._record_session_read_path(f"/tmp/f{i}.txt")
        self.assertLessEqual(
            len(brain._read_doc_cache_session_paths()),
            brain._SESSION_READ_PATHS_MAX,
        )


if __name__ == "__main__":
    unittest.main()
