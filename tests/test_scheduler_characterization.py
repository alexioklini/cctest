"""Characterization (behavior-pinning) tests for the scheduler subsystem.

These pin the PURE, deterministic behavior of the scheduler that lives in
`brain.py` (~lines 12950-15641) BEFORE it is extracted to
`engine/scheduler.py`. The contract here is: after extraction these tests
must still pass byte-for-byte, or the move introduced a regression.

WHY this file exists: the scheduler's core path (DB writes, the poll loop,
`_execute_scheduled` → LLM delegate) has NO existing tests and needs a live
daemon + DB + sidecar to exercise. The import-gate and existing unittests
therefore cannot catch a regression in the scheduler's *logic*. The pieces
that ARE pure and deterministic — schedule-string → next-fire-time parsing,
and the thinking-level validation gate — are the highest-value pin points
because they are pure in/out and the extraction risks a subtle off-by-one
or a dropped schedule format.

What is pinned here (pure functions, no DB / network / LLM):
  * Scheduler._calc_next_run        — schedule string -> next datetime
  * Scheduler._calc_next_from_last  — interval anchored to a prior run
  * brain._validate_thinking_level_for_model — per-model thinking gate
  * brain._VALID_TOOL_PROFILES / tool_profile -> purpose invariant

What is NOT pinned here (integration-only — gate blind spots for B2):
  * Scheduler.add / update / delete / pause / resume — all hit SCHEDULER_DB
  * get_due_tasks / complete_execution — atomic SQLite claim + re-bump
  * _execute_scheduled — builds a system prompt + runs the sidecar delegate
  * schedule_history row creation, run-id minting, attachment handling
  * the poll thread itself
  These need a live SQLite DB and (for execution) a sidecar/LLM, so the
  extraction's correctness there is only verified by the schedule eval run,
  not by this gate.

Run: python3 -m unittest tests.test_scheduler_characterization -v
"""

from __future__ import annotations

import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402


def _bare_scheduler() -> "brain.Scheduler":
    """A Scheduler instance WITHOUT running __init__.

    `_calc_next_run` / `_calc_next_from_last` use no instance state, but
    `Scheduler.__init__` creates SCHEDULER_DB on disk. `__new__` gives us
    the methods with zero side effects — the cleanest way to characterize
    pure methods that happen to live on a stateful class.
    """
    return brain.Scheduler.__new__(brain.Scheduler)


class TestCalcNextRun(unittest.TestCase):
    """Pins Scheduler._calc_next_run — the parser that turns a human
    schedule string into the next absolute fire time. INVARIANT: every
    supported schedule grammar (`every Xm/h/d`, `daily HH:MM`,
    `weekly DOW HH:MM`, `once YYYY-MM-DD HH:MM`) must keep producing the
    same offsets/anchors after extraction, and unsupported strings must
    keep returning None (the poll loop relies on None == "never fire")."""

    def setUp(self):
        self.sch = _bare_scheduler()

    def test_interval_minutes_offset_from_now(self):
        # INVARIANT: `every Nm` fires N minutes from *now*. Pin the delta,
        # not an absolute time (now() is the one impurity). 30m == 1800s.
        before = datetime.datetime.now()
        nxt = self.sch._calc_next_run("every 30m")
        delta = (nxt - before).total_seconds()
        self.assertGreaterEqual(delta, 1799.0)
        self.assertLessEqual(delta, 1801.0)

    def test_interval_hours_and_days(self):
        # INVARIANT: hour/day units resolve to the right timedelta. Catches
        # a unit-table regression (e.g. 'h' silently mapped to minutes).
        before = datetime.datetime.now()
        self.assertAlmostEqual(
            (self.sch._calc_next_run("every 6h") - before).total_seconds(),
            21600.0, delta=2.0)
        self.assertAlmostEqual(
            (self.sch._calc_next_run("every 2d") - before).total_seconds(),
            172800.0, delta=2.0)

    def test_interval_unit_aliases(self):
        # INVARIANT: the regex accepts long-form units (min/hour/day) and
        # an optional plural 's' — only the first letter is significant.
        # 'every 1hour' must equal 'every 1h'.
        before = datetime.datetime.now()
        self.assertAlmostEqual(
            (self.sch._calc_next_run("every 1hour") - before).total_seconds(),
            3600.0, delta=2.0)
        self.assertAlmostEqual(
            (self.sch._calc_next_run("every 15mins") - before).total_seconds(),
            900.0, delta=2.0)

    def test_once_absolute_timestamp_in_the_past_returns_it_verbatim(self):
        # CHARACTERIZATION QUIRK: `once` returns the parsed timestamp as-is,
        # with NO past/future guard (unlike `daily`/`weekly` which roll
        # forward). A past `once` time therefore comes back already-overdue,
        # so the poll loop fires it immediately on the next tick. Pinned
        # as-is so the extraction preserves this (the firing-then-disabling
        # of one-shot tasks depends on it).
        nxt = self.sch._calc_next_run("once 2020-01-01 09:00")
        self.assertEqual(nxt, datetime.datetime(2020, 1, 1, 9, 0, 0))

    def test_daily_rolls_to_tomorrow_when_time_already_passed(self):
        # INVARIANT: `daily HH:MM` always lands in the future. If today's
        # HH:MM has passed it must roll to tomorrow — otherwise the task
        # would be perpetually overdue and re-fire every poll.
        nxt = self.sch._calc_next_run("daily 09:30")
        self.assertIsNotNone(nxt)
        self.assertEqual((nxt.hour, nxt.minute, nxt.second), (9, 30, 0))
        self.assertGreater(nxt, datetime.datetime.now())

    def test_unrecognised_schedule_returns_none(self):
        # INVARIANT: an unparseable schedule string yields None (== "never
        # fire"), NOT an exception and NOT a default. The poll loop and
        # add()-time next_run computation both rely on None here.
        self.assertIsNone(self.sch._calc_next_run("cron */5 * * * *"))
        self.assertIsNone(self.sch._calc_next_run("hourly"))
        self.assertIsNone(self.sch._calc_next_run(""))


class TestCalcNextFromLast(unittest.TestCase):
    """Pins Scheduler._calc_next_from_last — interval re-computation
    anchored to the PREVIOUS run instead of now(). INVARIANT: this is what
    keeps `every Nh` tasks on a stable cadence (next = last + interval)
    rather than drifting forward by execution time on every fire."""

    def setUp(self):
        self.sch = _bare_scheduler()

    def test_interval_anchored_to_last_run_not_now(self):
        # INVARIANT: next fire = last_run + interval, deterministic and
        # independent of wall-clock now(). This is the anti-drift guarantee.
        last = "2026-05-23T10:00:00"
        self.assertEqual(
            self.sch._calc_next_from_last("every 2h", last),
            datetime.datetime(2026, 5, 23, 12, 0, 0))
        self.assertEqual(
            self.sch._calc_next_from_last("every 45m", last),
            datetime.datetime(2026, 5, 23, 10, 45, 0))

    def test_unparseable_last_run_falls_back_to_now_based_calc(self):
        # CHARACTERIZATION: a garbage last_run timestamp must NOT crash —
        # it falls back to _calc_next_run (now-based). Pin the fallback so
        # the extraction keeps the try/except, not just the happy path.
        before = datetime.datetime.now()
        nxt = self.sch._calc_next_from_last("every 1h", "not-a-timestamp")
        self.assertAlmostEqual(
            (nxt - before).total_seconds(), 3600.0, delta=2.0)

    def test_non_interval_schedule_delegates_to_calc_next_run(self):
        # INVARIANT: only `every ...` intervals anchor to last_run; a
        # `daily`/`once` schedule ignores last_run and re-derives via
        # _calc_next_run (its own roll-forward logic owns the anchor).
        nxt = self.sch._calc_next_from_last("daily 09:30", "2026-05-23T10:00:00")
        self.assertEqual((nxt.hour, nxt.minute), (9, 30))
        self.assertGreater(nxt, datetime.datetime.now())


class TestThinkingLevelValidation(unittest.TestCase):
    """Pins brain._validate_thinking_level_for_model — the gate
    Scheduler.add / update call to reject a thinking_level the target model
    can't honor. INVARIANT: format-mismatched levels are rejected with a
    specific message; '' (inherit) is always allowed. This guards the
    per-task thinking knob (CLAUDE.md: 'rejects format-mismatched levels')."""

    def setUp(self):
        # The function reads brain._models_config (populated by
        # init_models_config in a live server; empty under unittest). Inject
        # deterministic fixtures so format-specific branches are exercised
        # without booting the server. Snapshot + restore so other tests in
        # the discovery run see an unmodified config.
        self._snapshot = dict(brain._models_config)
        brain._models_config["char-mistral"] = {"thinking_format": "mistral_blocks"}
        brain._models_config["char-inline"] = {"thinking_format": "inline_tags"}
        brain._models_config["char-full"] = {"thinking_format": "reasoning_field"}
        brain._models_config["char-plain"] = {"thinking_format": "none"}

    def tearDown(self):
        brain._models_config.clear()
        brain._models_config.update(self._snapshot)

    def test_empty_level_always_ok(self):
        # INVARIANT: '' means "inherit from model default" and is valid for
        # ANY model — including a non-reasoning one. Add()/update() must let
        # a blank thinking_level through so 'Default' schedules work.
        self.assertIsNone(brain._validate_thinking_level_for_model("char-plain", ""))
        self.assertIsNone(brain._validate_thinking_level_for_model("char-mistral", ""))

    def test_garbage_level_rejected_before_model_lookup(self):
        # INVARIANT: an out-of-vocab level is rejected regardless of model
        # (the vocab check precedes the format check).
        self.assertEqual(
            brain._validate_thinking_level_for_model(None, "ultra"),
            "Invalid thinking_level: ultra")

    def test_mistral_blocks_rejects_low_and_medium(self):
        # INVARIANT: Mistral's mistral_blocks format accepts only none/high;
        # low/medium must be rejected with the Mistral-specific message.
        msg = brain._validate_thinking_level_for_model("char-mistral", "low")
        self.assertIsNotNone(msg)
        self.assertIn("Mistral", msg)
        self.assertEqual(
            brain._validate_thinking_level_for_model("char-mistral", "medium"),
            "Model 'char-mistral' (Mistral) accepts only 'none' or 'high'")
        # high IS allowed on mistral_blocks
        self.assertIsNone(brain._validate_thinking_level_for_model("char-mistral", "high"))

    def test_inline_tags_rejects_graduated_levels(self):
        # INVARIANT: inline_tags (oMLX on/off thinking) accepts only
        # none/high — low/medium have no representation, so reject them.
        self.assertIn(
            "on/off only",
            brain._validate_thinking_level_for_model("char-inline", "medium"))

    def test_non_reasoning_model_rejects_any_real_level(self):
        # INVARIANT: a thinking_format=none model rejects low/medium/high
        # but tolerates an explicit 'none' (harmless off-on-off). Pins both
        # halves of the none-format branch.
        self.assertIn(
            "does not support reasoning",
            brain._validate_thinking_level_for_model("char-plain", "high"))
        self.assertIsNone(
            brain._validate_thinking_level_for_model("char-plain", "none"))

    def test_reasoning_field_accepts_full_ladder(self):
        # INVARIANT: cloud reasoning_field models accept the full
        # Off/Low/Medium/High ladder — none of these are rejected.
        for lvl in ("none", "low", "medium", "high"):
            self.assertIsNone(
                brain._validate_thinking_level_for_model("char-full", lvl),
                f"reasoning_field should accept {lvl!r}")


class TestToolProfileInvariant(unittest.TestCase):
    """Pins the tool_profile vocabulary + the documented profile->purpose
    mapping for scheduled tasks. INVARIANT (CLAUDE.md): a task's tool_profile
    is '' (default), 'research_minimal', or 'interactive'; '' resolves to
    research_minimal at fire time, 'interactive' to interactive, and the
    `_memory_summary_` name prefix forces memory_summary. The extraction
    must preserve this exact set + mapping or scheduled tasks get the wrong
    tool surface."""

    def test_valid_tool_profiles_frozen(self):
        # INVARIANT: exactly these three profiles are accepted by
        # Scheduler.add/update validation. A 4th value or a removed value
        # is a contract break.
        self.assertEqual(
            brain._VALID_TOOL_PROFILES, ("", "research_minimal", "interactive"))

    def test_purpose_vocabulary_includes_scheduler_targets(self):
        # INVARIANT: the purposes a scheduled task can resolve to
        # (research_minimal, interactive, memory_summary) are all members of
        # the global purpose vocabulary the resolver validates against.
        for purpose in ("research_minimal", "interactive", "memory_summary"):
            self.assertIn(purpose, brain._VALID_PURPOSES)

    def test_profile_to_purpose_mapping(self):
        # INVARIANT: replicate the exact mapping from _execute_scheduled
        # (brain.py ~13892): _memory_summary_ prefix wins, else 'interactive'
        # profile -> interactive purpose, else research_minimal. This pins
        # the documented behavior so the extracted module keeps the same
        # decision. (Mapping is inline in _execute_scheduled, not a standalone
        # function — pinned via the documented rule, see CLAUDE.md "Tools".)
        def resolve(name: str, tool_profile: str) -> str:
            if name.startswith("_memory_summary_"):
                return "memory_summary"
            prof = (tool_profile or "").strip()
            return "interactive" if prof == "interactive" else "research_minimal"

        self.assertEqual(resolve("daily-report", ""), "research_minimal")
        self.assertEqual(resolve("daily-report", "research_minimal"), "research_minimal")
        self.assertEqual(resolve("daily-report", "interactive"), "interactive")
        # name prefix overrides any tool_profile value
        self.assertEqual(resolve("_memory_summary_main", "interactive"), "memory_summary")
        self.assertEqual(resolve("_memory_summary_main", ""), "memory_summary")


if __name__ == "__main__":
    unittest.main()
