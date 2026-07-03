"""Stream-stability semantics of the in-process loop's drain (v9.277.0).

The regression class (user report 2026-07-03): since the sidecar→in-process
migration, an upstream stream that died mid-generation was silently accepted
as a FINISHED answer (stop_reason ""), so the user had to type "continue" by
hand. The Anthropic SDK previously raised on incomplete streams and retried
connection flakes; the bare urllib drain replaced that with nothing.

Intent guarded here:
- got_done is the completeness signal: True ONLY when the [DONE] marker
  arrived; EOF or a socket death mid-stream returns the PARTIAL result with
  got_done False (never raises) so run_loop can auto-resume the round.
- A provider error delivered INSIDE the 200-SSE stream (data: {"error":...})
  is captured on error_payload instead of draining to an empty round (which
  read as "model said nothing" and triggered the misleading nudge loop).
- The byte-bound guard still raises (a runaway stream must kill the round).
- Connect-phase retry classification: transient network errors and
  retry-safe HTTP statuses retry; other 4xx fail immediately.

Run: python3 -m unittest tests.test_llm_loop_stream_stability
"""
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine.llm_loop as ll  # noqa: E402


def _mk(lines):
    return [line.encode() for line in lines]


def _noop(kind, text):
    pass


class TestDrainCompleteness(unittest.TestCase):
    def test_done_marker_sets_got_done(self):
        rr = ll._drain_openai_stream(_mk([
            'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":null}]}\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n',
            'data: [DONE]\n',
        ]), _noop, 1)
        self.assertTrue(rr.got_done)
        self.assertEqual(rr.finish_reason, "stop")
        self.assertEqual(rr.text, "Hello")

    def test_eof_without_done_is_truncated_partial(self):
        rr = ll._drain_openai_stream(_mk([
            'data: {"choices":[{"delta":{"content":"Par"},"finish_reason":null}]}\n',
            'data: {"choices":[{"delta":{"content":"tial"},"finish_reason":null}]}\n',
        ]), _noop, 1)
        self.assertFalse(rr.got_done)
        self.assertEqual(rr.text, "Partial")

    def test_socket_death_returns_partial_not_raise(self):
        class Dying:
            def __iter__(self):
                yield b'data: {"choices":[{"delta":{"content":"Half"},"finish_reason":null}]}\n'
                raise ConnectionResetError("peer reset")
        rr = ll._drain_openai_stream(Dying(), _noop, 1)
        self.assertFalse(rr.got_done)
        self.assertEqual(rr.text, "Half")

    def test_provider_error_event_captured(self):
        rr = ll._drain_openai_stream(_mk([
            'data: {"error":{"message":"upstream quota exceeded","code":429}}\n',
        ]), _noop, 1)
        self.assertIsNotNone(rr.error_payload)
        self.assertEqual(rr.error_payload.get("code"), 429)

    def test_byte_bound_still_raises(self):
        huge = b"x" * (ll._MAX_STREAM_BYTES + 1)
        with self.assertRaises(RuntimeError):
            ll._drain_openai_stream([huge], _noop, 1)


class TestConnectRetryClassification(unittest.TestCase):
    def test_transient_network_errors_retry(self):
        for e in (ConnectionResetError("x"), TimeoutError("x"),
                  urllib.error.URLError("unreachable"), OSError(54, "reset")):
            self.assertTrue(ll._is_retryable_connect_error(e), repr(e))

    def test_retry_safe_http_statuses(self):
        for code in (408, 429, 500, 502, 503, 504):
            e = urllib.error.HTTPError("u", code, "msg", None, None)
            self.assertTrue(ll._is_retryable_connect_error(e), code)

    def test_client_errors_fail_immediately(self):
        for code in (400, 401, 403, 404, 422):
            e = urllib.error.HTTPError("u", code, "msg", None, None)
            self.assertFalse(ll._is_retryable_connect_error(e), code)


if __name__ == "__main__":
    unittest.main()
