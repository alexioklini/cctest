"""The two NON-VOLUNTARY brakes on an agentic turn: the round cap and the cost gate.

Both stop a turn the model did NOT choose to end, so both must FAIL LOUD — the
loop hands back the text it had produced, and `stop_detail` carries a reason the
caller shows the user. Until v9.312.11 the round cap set `stop_reason="max_rounds"`
and nobody read it: a code-mode chat hit the 25-round cap one tool call short of
its report, returned its partial text with no warning, and the user took the
fragment for a finished answer.

Run: python3 -m unittest tests.test_loop_brakes
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import llm_loop  # noqa: E402


class LoopBrakeTests(unittest.TestCase):
    """Drives the REAL run_loop; only the transport + drain are faked, so the
    round loop, the brake checks and the summary assembly are the live code."""

    def setUp(self):
        self._orig_urlopen = llm_loop.urllib.request.urlopen
        self._orig_drain = llm_loop._drain_openai_stream
        self._orig_dispatch = llm_loop.dispatch_tool

        llm_loop.urllib.request.urlopen = lambda req, timeout=None: object()

        def _drain(resp, emit_delta, round_no, *a, **kw):
            # A model that ALWAYS calls a tool → would loop forever unbraked.
            rr = llm_loop._RoundResult()
            rr.tool_calls = {0: {"id": f"t{round_no}", "name": "noop", "args": "{}"}}
            rr.finish_reason = "tool_calls"
            rr.usage = {"prompt_tokens": 10, "completion_tokens": 5}
            rr.got_done = True
            return rr

        llm_loop._drain_openai_stream = _drain
        llm_loop.dispatch_tool = lambda name, args: ("ok", False)

    def tearDown(self):
        llm_loop.urllib.request.urlopen = self._orig_urlopen
        llm_loop._drain_openai_stream = self._orig_drain
        llm_loop.dispatch_tool = self._orig_dispatch

    def _run(self, **over):
        kw = dict(
            model="m", system_prompt="s", messages=[{"role": "user", "content": "x"}],
            tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
            allowed_tools=["noop"], max_tokens=100, sampling={}, thinking_level=None,
            disable_parallel_tool_use=False, prompt_cache_key="", forced_tool=None,
            api_key="k", base_url="http://x", emit=lambda t, d: None,
            is_cancelled=lambda: False, max_rounds=3)
        kw.update(over)
        return llm_loop.run_loop(**kw)

    def test_round_cap_reports_itself(self):
        """The cap is a TRUNCATION, not a finished answer — it must say so."""
        s = self._run(max_rounds=3)
        self.assertEqual(s["stop_reason"], "max_rounds")
        self.assertEqual(s["rounds"], 3)
        self.assertIn("Runden-Limit", s["stop_detail"],
                      "a capped turn must carry a reason the user can see")

    def test_budget_gate_stops_mid_turn(self):
        """The cost brake must end the turn where it fires — not at max_rounds."""
        calls = {"n": 0}

        def gate():
            calls["n"] += 1
            return "Kostenlimit erreicht (daily quota exhausted)" if calls["n"] >= 2 else None

        s = self._run(max_rounds=50, budget_gate=gate)
        self.assertEqual(s["stop_reason"], "budget")
        self.assertLess(s["rounds"], 50, "the brake did not stop the runaway turn")
        self.assertIn("Kostenlimit", s["stop_detail"])

    def test_budget_gate_keeps_partial_text(self):
        """Graceful: a budget stop KEEPS what was produced (no rollback, no raise)."""
        def _drain_with_text(resp, emit_delta, round_no, *a, **kw):
            rr = llm_loop._RoundResult()
            rr.text = f"Zwischenstand {round_no}"
            rr.tool_calls = {0: {"id": f"t{round_no}", "name": "noop", "args": "{}"}}
            rr.finish_reason = "tool_calls"
            rr.usage = {"prompt_tokens": 10, "completion_tokens": 5}
            rr.got_done = True
            return rr
        llm_loop._drain_openai_stream = _drain_with_text

        s = self._run(max_rounds=50, budget_gate=lambda: "Kostenlimit erreicht")
        self.assertEqual(s["stop_reason"], "budget")
        self.assertIn("Zwischenstand", s["final_text"],
                      "a budget stop must not discard the work already paid for")

    def test_broken_gate_never_kills_a_healthy_turn(self):
        """A failing budget check is a bug in the CHECK — the turn must survive it."""
        def boom():
            raise RuntimeError("quota db down")
        s = self._run(max_rounds=3, budget_gate=boom)
        self.assertEqual(s["stop_reason"], "max_rounds")

    def test_no_gate_is_unchanged(self):
        """Background/eval turns pass no gate — behaviour must be untouched."""
        s = self._run(max_rounds=2)
        self.assertEqual(s["stop_reason"], "max_rounds")
        self.assertEqual(s["rounds"], 2)

    def test_gate_not_called_on_first_round(self):
        """Round 1 has cost nothing yet — gating it would refuse the turn outright
        (that is the PRE-FLIGHT gate's job, which can still swap to a local model).
        A gate that always stops therefore lets round 1 run and blocks at the
        round-2 boundary — BEFORE round 2 is paid for, so `rounds` stays 1."""
        seen = {"n": 0}

        def gate():
            seen["n"] += 1
            return "stop"

        s = self._run(max_rounds=5, budget_gate=gate)
        self.assertEqual(s["rounds"], 1, "round 1 must run un-gated")
        self.assertEqual(seen["n"], 1, "gate is checked once, at the round-2 boundary")
        self.assertEqual(s["stop_reason"], "budget")


class AgentLimitsPrecedenceTests(unittest.TestCase):
    """`_get_agent_limits` is the single resolver for runtime limits. The chat +
    scheduler paths used to read agent.json directly with their own hardcoded 25,
    so the model-profile layer was dead code and two defaults disagreed."""

    def test_round_cap_default_is_the_runaway_brake(self):
        import brain
        self.assertEqual(brain.AGENT_LIMITS_DEFAULTS["max_tool_rounds"],
                         brain.MAX_TOOL_ROUNDS)
        self.assertGreaterEqual(
            brain.MAX_TOOL_ROUNDS, 80,
            "the cap bounds a RUNAWAY turn; it must not bound ordinary agentic work")

    def test_no_profile_overrides_the_round_cap(self):
        """Profiles tune COST knobs, not how many rounds a job needs. Now that the
        resolver is wired, a value here would suddenly bite — keep them absent."""
        from engine.model_select import MODEL_PROFILES
        for name, prof in MODEL_PROFILES.items():
            self.assertNotIn(
                "max_tool_rounds", prof.get("limits", {}) or {},
                f"profile '{name}' would silently cap rounds — see model_select.py")


if __name__ == "__main__":
    unittest.main()
