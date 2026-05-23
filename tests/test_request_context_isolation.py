"""Concurrency / no-request-state-bleed gate for the Tier-G thread-local refactor.

This is the headline acceptance criterion of the thread-local refactor: the
property that two overlapping requests on a shared worker pool each see ONLY
their own request context, and that teardown leaves no residue for the next
task on the same worker thread.

That property had ZERO coverage before this test. It is written to pass against
the CURRENT raw `threading.local()` design FIRST (establishing a correct green
baseline), then must keep passing after the migration to the typed
ContextVar-backed RequestContext + shim.

Runs in the bare test interpreter — no server, no spaCy, no network.
"""

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from engine.context import _thread_local, request_context, get_request_context, RequestContext


# The core request-identity fields exercised by the bleed test. (A representative
# subset of the full inventory — enough to prove cross-thread isolation + teardown.)
_FIELDS = ("current_user_id", "current_session_id", "current_agent", "project")


def _set_ctx(values: dict):
    for k, v in values.items():
        setattr(_thread_local, k, v)


def _teardown_ctx():
    """Mirror the production manual teardown (the pattern Tier-G replaces)."""
    _thread_local.current_user_id = ""
    _thread_local.current_session_id = None
    _thread_local.current_agent = None
    _thread_local.project = None


def _read_ctx() -> dict:
    return {
        "current_user_id": getattr(_thread_local, "current_user_id", ""),
        "current_session_id": getattr(_thread_local, "current_session_id", None),
        "current_agent": getattr(_thread_local, "current_agent", None),
        "project": getattr(_thread_local, "project", None),
    }


class TestRequestContextIsolation(unittest.TestCase):
    def test_no_bleed_under_overlap(self):
        """N tasks on a small shared pool, interleaved by sleeps, each reads back
        only its own values at every checkpoint — never another task's."""
        n = 24
        pool_size = 4
        errors = []
        barrier = threading.Barrier(pool_size)

        def task(i):
            mine = {
                "current_user_id": f"user-{i}",
                "current_session_id": f"sess-{i}",
                "current_agent": f"agent-{i}",
                "project": f"proj-{i}",
            }
            try:
                _set_ctx(mine)
                # Force overlap: small staggered sleeps so multiple tasks are
                # mid-flight on the shared pool simultaneously.
                for _ in range(5):
                    time.sleep(0.001 * ((i % 3) + 1))
                    got = _read_ctx()
                    if got != mine:
                        errors.append((i, mine, got))
            finally:
                _teardown_ctx()

        # Sync the first wave so the pool threads genuinely overlap.
        def task_synced(i):
            if i < pool_size:
                try:
                    barrier.wait(timeout=5)
                except threading.BrokenBarrierError:
                    pass
            task(i)

        with ThreadPoolExecutor(max_workers=pool_size) as ex:
            list(ex.map(task_synced, range(n)))

        self.assertEqual(errors, [], f"context bleed detected: {errors[:3]}")

    def test_teardown_leaves_no_residue(self):
        """After teardown, a task reusing the same worker thread sees defaults,
        not the previous task's values. This is what the manual `=None` blocks
        (and, post-migration, the context-manager token-reset) guarantee."""
        leaked = []
        # Single worker -> the second task is guaranteed to reuse the thread.
        with ThreadPoolExecutor(max_workers=1) as ex:
            def first():
                _set_ctx({
                    "current_user_id": "leaky-user",
                    "current_session_id": "leaky-sess",
                    "current_agent": "leaky-agent",
                    "project": "leaky-proj",
                })
                _teardown_ctx()

            def second():
                got = _read_ctx()
                # None of the first task's values may survive.
                if "leaky" in str(got.values()):
                    leaked.append(got)

            ex.submit(first).result()
            ex.submit(second).result()

        self.assertEqual(leaked, [], f"teardown residue bled to next task: {leaked}")

    def test_negative_control_skipped_teardown_bleeds(self):
        """NEGATIVE CONTROL: documents what this gate actually catches. If a task
        sets context and SKIPS teardown, the next task on the same worker thread
        DOES see the stale values. This proves the test is sensitive to the exact
        bug class (missed teardown -> bleed) the refactor eliminates structurally.
        """
        observed = []
        with ThreadPoolExecutor(max_workers=1) as ex:
            def first_no_teardown():
                _set_ctx({
                    "current_user_id": "stale-user",
                    "current_session_id": "stale-sess",
                    "current_agent": "stale-agent",
                    "project": "stale-proj",
                })
                # Deliberately NO teardown.

            def second():
                observed.append(_read_ctx())

            ex.submit(first_no_teardown).result()
            ex.submit(second).result()

        # On the raw threading.local() design with skipped teardown, the stale
        # values bleed. The test asserts the bleed IS observable (so we know the
        # other tests would catch a real missed-teardown regression).
        self.assertTrue(
            any("stale" in str(o.values()) for o in observed),
            "negative control did not reproduce bleed — the bleed tests would be "
            "vacuously green; investigate before trusting the gate.",
        )
        # Clean up so we don't pollute other tests sharing the interpreter.
        _teardown_ctx()


class TestRequestContextShim(unittest.TestCase):
    """Locks in the Phase-1 ContextVar-backed shim contract: the old
    `_thread_local` name and the new accessors share the SAME storage, declared
    fields read their typed default when unset, dynamic keys route to a bucket,
    and `request_context(...)` nests + restores."""

    def setUp(self):
        # Start each test from a clean top-level context.
        from engine import context as ec
        ec.clear_thread_context()

    def test_shim_and_accessor_share_storage(self):
        _thread_local.current_user_id = "shared-U"
        self.assertEqual(get_request_context().current_user_id, "shared-U")

    def test_unset_field_returns_typed_default(self):
        self.assertIs(getattr(_thread_local, "plan_mode"), False)
        self.assertEqual(getattr(_thread_local, "caveman_system"), 0)
        self.assertEqual(getattr(_thread_local, "audit_source"), "chat")

    def test_dynamic_key_escape_hatch(self):
        setattr(_thread_local, "_artifact_folder_sX", "2026-05-23_sX")
        self.assertEqual(getattr(_thread_local, "_artifact_folder_sX", None), "2026-05-23_sX")
        self.assertIsNone(getattr(_thread_local, "_artifact_folder_other", None))

    def test_unknown_attr_raises_so_getattr_default_applies(self):
        with self.assertRaises(AttributeError):
            _thread_local.no_such_attr_zzz

    def test_context_manager_nests_and_restores(self):
        _thread_local.project = "outer"
        with request_context(project="inner", current_user_id="U-in"):
            self.assertEqual(_thread_local.project, "inner")
            self.assertEqual(_thread_local.current_user_id, "U-in")
            # nested again
            with request_context(project="innermost"):
                self.assertEqual(_thread_local.project, "innermost")
            self.assertEqual(_thread_local.project, "inner")
        self.assertEqual(_thread_local.project, "outer")


if __name__ == "__main__":
    unittest.main()
