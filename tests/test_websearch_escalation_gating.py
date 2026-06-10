"""Data-layer guards for the "memory-first then web, never get stuck in
memory" behaviour and the tool-agnostic research disciplines.

Background (2026-06-10, memory note project_websearch_skip_root_cause): a long
debugging saga where mistral-small/medium would search only mempalace, find
nothing, and GIVE UP instead of escalating to the web tools that were present.
The root cause was NOT tool-gating (we chased that through 6 reverted versions)
— it was two things in config.json:

  1. read_document's description anchored the model onto a "project memory flow"
     (referenced mempalace_query) even on pure-web turns. Since read_document is
     ALWAYS present, that anchor fired every turn.
  2. research_mode_disciplines.refusal was memory-specific ("mempalace returns 0
     → refuse, give up") and is injected DYNAMICALLY on every grounding turn,
     incl. web-only → told the model to give up after empty memory.

These tests lock in the PRECONDITIONS of the fixed behaviour, deterministically,
without a live LLM:

  A. Tool-resolution invariants across LLM-tool-optimization on/off ×
     memory-tool deferred/present — the right tools are in the set.
  B. The research disciplines (refusal/precision/citation) are TOOL-AGNOSTIC —
     they must not name mempalace / read_document / web_fetch etc., so the
     refusal text reads the same on a web turn as on a memory turn (and never
     says "give up after empty memory").
  C. An ALWAYS-PRESENT tool's prompt prose must not reference a tool that is
     NOT guaranteed to be present alongside it (deferred / disabled), so a
     deferred mempalace can't leave a dangling "see mempalace_query" anchor in
     read_document.

The actual model BEHAVIOUR (does it really escalate? does it refuse without
hallucinating when web is off and memory is empty?) is non-deterministic and
lives in eval/websearch_escalation_eval.py (needs the sidecar + a real model).

Run:  python3 -m unittest tests.test_websearch_escalation_gating -v
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402  (inits the runtime: TOOL_DISPATCH, TOOL_GROUPS, globals)
from engine.context import request_context  # noqa: E402


def setUpModule():
    """A bare `import brain` has NO server_config → `_tool_settings` and
    `_research_mode_disciplines` are empty, and the prose audits would all skip
    (memory note feedback_never_probe_server_config_via_import). The server sets
    these globals from config.json at boot; we replicate exactly that one
    assignment so the tests audit the REAL prose the running server uses. If
    config.json is absent the tests fall back to self-skip (CI without a config)."""
    cfg_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    if not os.path.exists(cfg_path):
        return
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if cfg.get("tool_settings"):
        brain._tool_settings = cfg["tool_settings"]
    if cfg.get("research_mode_disciplines"):
        brain._research_mode_disciplines = cfg["research_mode_disciplines"]


# ── Tools that are ALWAYS present (the never-strip floor + globally-on,
#    never-deferred built-ins). A description on any of these may only reference
#    other tools in this set. mempalace_* and the KG tools are deferred per the
#    main agent, so they are NOT here.
ALWAYS_PRESENT = {
    "read_file", "write_file", "edit_file", "list_directory", "search_files",
    "execute_command", "tool_search", "ask_user",
    "read_document", "write_document", "edit_document",
    "web_fetch", "searxng_search", "python_exec",
}

# Tools loaded together when the memory group is pulled in via tool_search.
MEMORY_GROUP = {
    "mempalace_query", "save_chat_to_memory",
    "mempalace_kg_query", "mempalace_kg_search", "mempalace_kg_neighbors",
}

# Substrings that betray a tool-/mechanism-specific reference. A tool-agnostic
# discipline must contain NONE of these.
TOOL_SPECIFIC_TERMS = [
    "mempalace", "read_document", "web_fetch", "searxng", "exa_search",
    "drawer", "source_file", ".brain-extracted",
]


def _ts():
    """The live tool_settings the running config produced. Falls back to {} when
    run as a bare `import brain` (no server_config) — tests that need real prose
    skip themselves in that case rather than assert on defaults (memory note
    feedback_never_probe_server_config_via_import)."""
    return getattr(brain, "_tool_settings", None) or {}


class TestClassifierDeferralInvariants(unittest.TestCase):
    """classifier_tool_deferral is the LLM-tool-optimization seam. Whatever it
    decides, the structural floor must survive and a needed group must be pulled
    in — so the model never has to tool_search for a tool the classifier already
    flagged as needed."""

    def test_floor_tools_never_deferred(self):
        # Classifier says the turn only needs web. core/workflows are the floor.
        defer_extra, undefer = brain.classifier_tool_deferral(
            model="CLIProxyAPI/mistral-small-latest", tool_groups=["web"])
        for floor_tool in ("tool_search", "ask_user"):
            self.assertNotIn(
                floor_tool, defer_extra,
                f"{floor_tool} must never be deferred — it's how the model "
                f"reaches deferred tools / clarifies")

    def test_needed_group_is_undeferred(self):
        # When the classifier flags `web`, the web tools must be force-pulled in
        # (undefer) so the model can use them WITHOUT a tool_search detour.
        _, undefer = brain.classifier_tool_deferral(
            model="CLIProxyAPI/mistral-small-latest", tool_groups=["web"])
        self.assertTrue(
            any(t in undefer for t in ("web_fetch", "searxng_search")),
            "web group flagged → web tools must be un-deferred (pulled in)")

    def test_memory_group_undeferred_when_flagged(self):
        # When the classifier flags `memory`, mempalace_query must be pulled in
        # so the model can do its memory-first lookup directly, not via tool_search.
        _, undefer = brain.classifier_tool_deferral(
            model="CLIProxyAPI/mistral-small-latest", tool_groups=["memory"])
        self.assertIn(
            "mempalace_query", undefer,
            "memory flagged → mempalace_query un-deferred (no tool_search hop)")

    def test_no_optimization_for_warm_protected_model(self):
        """LLM-tool-optimization OFF case: a warmup-protected model must get
        ([],[]) — its static KV prefix is never reshaped, so the tool set is the
        full static one (no deferral churn)."""
        saved = getattr(brain, "_models_config", None)
        try:
            # A model explicitly configured with warmup → protected.
            brain._models_config = {"warm-local-model": {"warmup": True}}
            defer_extra, undefer = brain.classifier_tool_deferral(
                model="warm-local-model", tool_groups=["web"])
            self.assertEqual((defer_extra, undefer), ([], []),
                             "warmup-protected model must not be reshaped")
        finally:
            if saved is not None:
                brain._models_config = saved

    def test_none_tool_groups_is_noop(self):
        # `None` = NO classifier signal (keyword fallback / down classifier) →
        # leave the static deferral as-is (fail-open).
        self.assertEqual(
            brain.classifier_tool_deferral(
                model="CLIProxyAPI/mistral-small-latest", tool_groups=None),
            ([], []))

    def test_empty_tool_groups_trims_to_floor(self):
        # `[]` = the classifier RAN and found NO needed groups (e.g. a greeting).
        # That is a strong "trim everything" signal, NOT no-signal: every group's
        # tools are deferred OUT except the structural floor (tool_search,
        # ask_user), and nothing is un-deferred. This is the 11k-"hi" fix — the
        # old behaviour treated [] as a no-op and kept the full tool prompt.
        defer_extra, undefer = brain.classifier_tool_deferral(
            model="CLIProxyAPI/mistral-small-latest", tool_groups=[])
        self.assertEqual(undefer, [], "empty groups un-defer nothing")
        self.assertTrue(defer_extra, "empty groups must defer the non-floor tools OUT")
        # The structural floor must never be deferred.
        for floor_tool in brain._TOOL_GATING_NEVER_STRIP_TOOLS:
            self.assertNotIn(floor_tool, defer_extra,
                             f"floor tool {floor_tool} must stay in-prompt")


class TestResearchDisciplinesAreToolAgnostic(unittest.TestCase):
    """The refusal/precision/citation disciplines are injected on EVERY
    grounding turn (memory, web, documents, context) — incl. a pure-web turn.
    They must therefore be tool-agnostic: never name a specific tool or its
    internal mechanics, and the refusal must NOT tell the model to give up after
    empty memory."""

    def setUp(self):
        self.disc = brain.render_research_mode_disciplines() or ""
        if not self.disc.strip():
            self.skipTest("no research disciplines configured (bare import / "
                          "empty config) — nothing to assert")

    def test_no_tool_specific_terms(self):
        leaked = [t for t in TOOL_SPECIFIC_TERMS
                  if re.search(re.escape(t), self.disc, re.I)]
        self.assertEqual(
            leaked, [],
            f"research disciplines must be tool-agnostic; leaked refs: {leaked}. "
            f"They fire on web turns too — a 'read_document'/'mempalace' ref "
            f"there is a dangling anchor.")

    def test_refusal_does_not_say_give_up_on_empty_memory(self):
        # The old refusal said: mempalace returns 0 → the project does NOT
        # contain it → refuse. On a web turn that suppressed escalation. The new
        # one must be about NOT FABRICATING, not about giving up after memory.
        low = self.disc.lower()
        self.assertNotIn("give up", low,
                         "refusal must not instruct giving up (it suppresses "
                         "web escalation)")
        # It MUST still carry the no-fabrication intent (the legitimate purpose).
        self.assertTrue(
            any(kw in low for kw in
                ("unproven", "fabricat", "not available", "invent")),
            "refusal must still forbid asserting unproven / invented facts")


class TestAlwaysPresentToolsHaveNoDanglingRefs(unittest.TestCase):
    """An ALWAYS-PRESENT tool's prose may only reference other ALWAYS-PRESENT
    tools. read_document is the canonical offender: it used to reference
    mempalace_query (deferred) — a dead anchor that pulled the model toward a
    memory flow on every turn. Guards that regression."""

    def setUp(self):
        if not _ts():
            self.skipTest("no tool_settings loaded (bare import) — skip prose "
                          "audit; the live server / eval covers it")

    def _prose(self, name):
        rec = _ts().get(name) or {}
        return " ".join((rec.get(f) or "")
                        for f in ("description", "when_to_use", "warnings", "examples"))

    def test_read_document_prose_has_no_mempalace_ref(self):
        prose = self._prose("read_document")
        for term in ("mempalace", "drawer", "read_path"):
            self.assertNotIn(
                term, prose,
                f"read_document (always-present) must not reference '{term}' "
                f"(mempalace is deferred → dangling memory-flow anchor)")

    def test_no_always_present_tool_references_a_deferred_tool(self):
        ts = _ts()
        all_names = set(ts.keys())
        problems = []
        for name in ALWAYS_PRESENT:
            if name not in ts:
                continue
            rec = ts[name]
            if rec.get("enabled", True) is False:
                continue
            prose = self._prose(name)
            for other in all_names:
                if other == name or other in ALWAYS_PRESENT:
                    continue
                if re.search(r"\b" + re.escape(other) + r"\b", prose):
                    problems.append(f"{name} → {other}")
        self.assertEqual(
            problems, [],
            f"always-present tools reference not-always-present tools: {problems}")


class TestResolveActiveToolsRespectsAgentDeferral(unittest.TestCase):
    """End-to-end through the real resolver: with the main agent's current
    config, the resolved interactive tool set must be coherent — tool_search is
    always present (so deferred tools are reachable), and a deferred memory tool
    is NOT in the initial set but IS reachable via discovered_tools."""

    def _names(self, discovered=None):
        with request_context(current_session_id="wse-test", project=""):
            tools = brain.resolve_active_tools(
                purpose="interactive", agent_id="main",
                discovered_tools=discovered or set())
        return {t["name"] for t in tools}

    def test_tool_search_always_in_set(self):
        names = self._names()
        self.assertIn("tool_search", names,
                      "tool_search must always be present so deferred tools "
                      "are reachable")

    def test_deferred_memory_tool_pulled_in_when_discovered(self):
        # If mempalace_query is deferred for the main agent, it should be absent
        # from the bare set but appear once 'discovered' (tool_search loaded it).
        bare = self._names()
        if "mempalace_query" in bare:
            self.skipTest("mempalace_query is not deferred for the main agent "
                          "in the current config — nothing to assert here")
        with_disc = self._names(discovered={"mempalace_query"})
        self.assertIn(
            "mempalace_query", with_disc,
            "a deferred tool must become available once discovered via tool_search")


if __name__ == "__main__":
    unittest.main()
