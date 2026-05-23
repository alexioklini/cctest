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

from engine.context import request_context, get_request_context, RequestContext


# The core request-identity fields exercised by the bleed test. (A representative
# subset of the full inventory — enough to prove cross-thread isolation + teardown.)
_FIELDS = ("current_user_id", "current_session_id", "current_agent", "project")


def _set_ctx(values: dict):
    ctx = get_request_context()
    for k, v in values.items():
        setattr(ctx, k, v)


def _teardown_ctx():
    """Reset the request-identity fields to defaults — mirrors what the
    context-manager's token-reset does at top level. Used by the tests that
    deliberately exercise teardown WITHOUT the context-manager (the residue +
    negative-control tests) to prove the bleed property directly."""
    ctx = get_request_context()
    ctx.current_user_id = ""
    ctx.current_session_id = None
    ctx.current_agent = None
    ctx.project = None


def _read_ctx() -> dict:
    ctx = get_request_context()
    return {
        "current_user_id": ctx.current_user_id,
        "current_session_id": ctx.current_session_id,
        "current_agent": ctx.current_agent,
        "project": ctx.project,
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


class TestRequestContextAccessor(unittest.TestCase):
    """Locks in the ContextVar-backed accessor contract (post-shim-removal):
    `get_request_context()` returns the active RequestContext, declared fields
    read their typed default when unset, the `_dynamic` bucket holds arbitrary
    keys (the `_artifact_folder_*` pattern), and `request_context(...)` nests +
    restores. (Replaces the Phase-1 shim test once the `_thread_local` shim is
    gone.)"""

    def setUp(self):
        # Start each test from a clean top-level context.
        self.enterContext(request_context())

    def test_accessor_returns_active_context(self):
        get_request_context().current_user_id = "shared-U"
        self.assertEqual(get_request_context().current_user_id, "shared-U")

    def test_unset_field_returns_typed_default(self):
        ctx = get_request_context()
        self.assertIs(ctx.plan_mode, False)
        self.assertEqual(ctx.caveman_system, 0)
        self.assertEqual(ctx.audit_source, "chat")

    def test_dynamic_key_bucket(self):
        get_request_context()._dynamic["_artifact_folder_sX"] = "2026-05-23_sX"
        self.assertEqual(
            get_request_context()._dynamic.get("_artifact_folder_sX"), "2026-05-23_sX")
        self.assertIsNone(get_request_context()._dynamic.get("_artifact_folder_other"))

    def test_context_manager_nests_and_restores(self):
        get_request_context().project = "outer"
        with request_context(project="inner", current_user_id="U-in"):
            self.assertEqual(get_request_context().project, "inner")
            self.assertEqual(get_request_context().current_user_id, "U-in")
            # nested again
            with request_context(project="innermost"):
                self.assertEqual(get_request_context().project, "innermost")
            self.assertEqual(get_request_context().project, "inner")
        self.assertEqual(get_request_context().project, "outer")


if __name__ == "__main__":
    unittest.main()
