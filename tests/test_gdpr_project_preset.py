"""KYC-Projekt-Preset (L7a) + Degradations-Tally (L7b) — Overlay-Mechanik.

Das per-Projekt-Preset (`project.json → gdpr_preset`) überlagert die globale
`gdpr_scanner`-Config per Kopie (der 30s-Cache bleibt Preset-frei), damit ein
KYC-Projekt Scanner+Anonymisierung+Web-Consent erzwingt, ohne die globale
Config anzufassen.

Die wichtigsten Invarianten (PII_ANALYSIS_PARITY_HANDOVER.md §L7):
  - CACHE-SICHERHEIT: der Overlay mutiert NIE das gecachte Config-Dict
    (mehrere Caller halten dasselbe Objekt).
  - kyc: enabled=True + web_egress='ask' + name-Regel aus dem
    contact=ignore-Loch gehoben (§0.5) — aber NUR verstärkend: eine
    explizit stärkere Admin-Einstellung (name=block) bleibt.
  - kyc_local: enabled=True + background_pii_action='swap_to_local';
    web_egress bleibt unangetastet (lokale Turns haben kein Mapping).
  - Auflösung: expliziter `preset=`-Param > Request-Kontext
    (`gdpr_project_preset`, gesetzt von apply_domain_context) > global.
  - update_project validiert gdpr_preset und koppelt research_mode=True
    beim Aktivieren (expliziter research_mode im selben Update gewinnt).

Run: python3 -m unittest tests.test_gdpr_project_preset -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brain  # noqa: E402
from engine.context import request_context, get_request_context  # noqa: E402


def _base_cfg(**over):
    """Ein globales Config-Dict mit den Live-Defaults dieses Setups
    (contact=ignore → name ignore), config-unabhängig gepinnt."""
    cfg = {
        "enabled": False,
        "web_egress": "refuse",
        "background_pii_action": "anonymise",
        "categories": {"contact": {"action": "ignore"},
                       "personal": {"action": "warn"}},
        "rule_overrides": {},
    }
    cfg.update(over)
    return cfg


class TestPresetOverlay(unittest.TestCase):
    def test_kyc_overlay_fields(self):
        cfg = _base_cfg()
        out = brain._gdpr_apply_project_preset(cfg, "kyc")
        self.assertTrue(out["enabled"])
        self.assertEqual(out["web_egress"], "ask")
        self.assertEqual(out["rule_overrides"].get("name"), "warn")

    def test_kyc_local_overlay_fields(self):
        cfg = _base_cfg()
        out = brain._gdpr_apply_project_preset(cfg, "kyc_local")
        self.assertTrue(out["enabled"])
        self.assertEqual(out["background_pii_action"], "swap_to_local")
        # web_egress unangetastet — lokale Turns haben kein Mapping.
        self.assertEqual(out["web_egress"], "refuse")

    def test_overlay_never_mutates_source(self):
        """Cache-Sicherheit: das Eingangs-Dict (== gecachtes Objekt) bleibt
        byte-identisch — auch die verschachtelten rule_overrides."""
        cfg = _base_cfg()
        snapshot = json.dumps(cfg, sort_keys=True)
        out = brain._gdpr_apply_project_preset(cfg, "kyc")
        self.assertIsNot(out, cfg)
        self.assertIsNot(out["rule_overrides"], cfg["rule_overrides"])
        self.assertEqual(json.dumps(cfg, sort_keys=True), snapshot)

    def test_only_strengthen_name_rule(self):
        """Ein explizit stärkeres Admin-Setting (name=block) wird NIE auf
        warn heruntergestuft."""
        cfg = _base_cfg(rule_overrides={"name": "block"})
        out = brain._gdpr_apply_project_preset(cfg, "kyc")
        self.assertEqual(out["rule_overrides"]["name"], "block")

    def test_unknown_preset_is_identity(self):
        cfg = _base_cfg()
        self.assertIs(brain._gdpr_apply_project_preset(cfg, ""), cfg)
        self.assertIs(brain._gdpr_apply_project_preset(cfg, "bogus"), cfg)


class TestGetterResolution(unittest.TestCase):
    """_get_gdpr_scanner_config: Param > Kontext > global — gegen die echte
    Getter-Funktion (liest die Live-config.json), daher nur RELATIVE
    Assertions (Overlay-Wirkung), keine absoluten Config-Werte."""

    def test_explicit_param_wins_and_empty_forces_global(self):
        glob = brain._get_gdpr_scanner_config(preset="")
        kyc = brain._get_gdpr_scanner_config(preset="kyc")
        self.assertTrue(kyc["enabled"])
        self.assertEqual(kyc["web_egress"], "ask")
        # Der globale Rückgabewert wurde durch den Overlay-Call nicht verändert.
        self.assertEqual(glob, brain._get_gdpr_scanner_config(preset=""))

    def test_context_fallback(self):
        with request_context():
            get_request_context().gdpr_project_preset = "kyc"
            cfg = brain._get_gdpr_scanner_config()
            self.assertTrue(cfg["enabled"])
            self.assertEqual(cfg["web_egress"], "ask")
        # Außerhalb des Kontexts: global (kein Preset-Bleed).
        cfg2 = brain._get_gdpr_scanner_config()
        self.assertEqual(cfg2, brain._get_gdpr_scanner_config(preset=""))

    def test_param_overrides_context(self):
        with request_context():
            get_request_context().gdpr_project_preset = "kyc"
            cfg = brain._get_gdpr_scanner_config(preset="")
            self.assertEqual(cfg, brain._get_gdpr_scanner_config(preset=""))


class TestPresetForProject(unittest.TestCase):
    def test_resolves_valid_presets_and_rejects_garbage(self):
        with mock.patch.object(brain.ProjectManager, "get_project",
                               return_value={"gdpr_preset": "kyc"}):
            self.assertEqual(
                brain._gdpr_project_preset_for("main", "ko-kunden"), "kyc")
        with mock.patch.object(brain.ProjectManager, "get_project",
                               return_value={"gdpr_preset": "KYC_LOCAL"}):
            self.assertEqual(
                brain._gdpr_project_preset_for("main", "p"), "kyc_local")
        with mock.patch.object(brain.ProjectManager, "get_project",
                               return_value={"gdpr_preset": "bogus"}):
            self.assertEqual(brain._gdpr_project_preset_for("main", "p"), "")
        with mock.patch.object(brain.ProjectManager, "get_project",
                               return_value=None):
            self.assertEqual(brain._gdpr_project_preset_for("main", "p"), "")
        # Kein Projekt → nie ein Disk-Read.
        self.assertEqual(brain._gdpr_project_preset_for("main", ""), "")

    def test_get_project_failure_is_empty(self):
        with mock.patch.object(brain.ProjectManager, "get_project",
                               side_effect=RuntimeError("boom")):
            self.assertEqual(brain._gdpr_project_preset_for("main", "p"), "")


class TestApplyDomainContext(unittest.TestCase):
    def test_sets_preset_and_undefers_doc_checks(self):
        with mock.patch.object(
                brain.ProjectManager, "get_project",
                return_value={"gdpr_preset": "kyc"}):
            with request_context():
                brain.apply_domain_context(
                    agent_id="main", project="ko-kunden", user_id="")
                ctx = get_request_context()
                self.assertEqual(ctx.gdpr_project_preset, "kyc")
                for t in ("mrz_verify", "doc_dates_check",
                          "identity_consistency"):
                    self.assertIn(t, ctx.undefer_tools or [])

    def test_resets_preset_without_project(self):
        with request_context():
            get_request_context().gdpr_project_preset = "kyc"
            brain.apply_domain_context(agent_id="main", project="", user_id="")
            self.assertEqual(get_request_context().gdpr_project_preset, "")

    def test_no_preset_project_stays_empty(self):
        with mock.patch.object(brain.ProjectManager, "get_project",
                               return_value={"name": "x"}):
            with request_context():
                brain.apply_domain_context(
                    agent_id="main", project="x", user_id="")
                self.assertEqual(
                    get_request_context().gdpr_project_preset, "")

    def test_build_tool_context_snapshots_preset(self):
        with mock.patch.object(brain.ProjectManager, "get_project",
                               return_value={"gdpr_preset": "kyc_local"}):
            with request_context():
                brain.apply_domain_context(
                    agent_id="main", project="p", user_id="")
                tc = brain.build_tool_context(
                    session_id="s", agent_id="main")
                self.assertEqual(tc.get("gdpr_project_preset"), "kyc_local")


class TestApplyBgContext(unittest.TestCase):
    def test_bg_context_restores_preset(self):
        from handlers import sidecar_proxy
        with request_context():
            sidecar_proxy._apply_bg_context({
                "session_id": "s", "agent_id": "main",
                "gdpr_project_preset": "kyc",
            })
            self.assertEqual(
                get_request_context().gdpr_project_preset, "kyc")

    def test_bg_context_defaults_empty(self):
        from handlers import sidecar_proxy
        with request_context():
            sidecar_proxy._apply_bg_context({
                "session_id": "s", "agent_id": "main",
            })
            self.assertEqual(get_request_context().gdpr_project_preset, "")


class TestUpdateProjectPreset(unittest.TestCase):
    def _run_update(self, initial_cfg, updates):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "project.json"), "w") as f:
                json.dump(initial_cfg, f)
            with mock.patch.object(brain.ProjectManager, "_project_dir",
                                   return_value=td):
                res = brain.ProjectManager.update_project("main", "p", updates)
                self.assertNotIn("error", res, res)
                with open(os.path.join(td, "project.json")) as f:
                    return json.load(f)

    def test_activation_sets_research_mode(self):
        cfg = self._run_update({"name": "p", "id": "abc123def456"},
                               {"gdpr_preset": "kyc"})
        self.assertEqual(cfg["gdpr_preset"], "kyc")
        self.assertTrue(cfg["research_mode"])

    def test_explicit_research_mode_wins(self):
        cfg = self._run_update(
            {"name": "p", "id": "abc123def456"},
            {"gdpr_preset": "kyc", "research_mode": False})
        self.assertEqual(cfg["gdpr_preset"], "kyc")
        self.assertFalse(cfg["research_mode"])

    def test_invalid_preset_coerced_empty(self):
        cfg = self._run_update({"name": "p", "id": "abc123def456"},
                               {"gdpr_preset": "evil"})
        self.assertEqual(cfg["gdpr_preset"], "")

    def test_deactivation_does_not_touch_research_mode(self):
        cfg = self._run_update(
            {"name": "p", "id": "abc123def456", "gdpr_preset": "kyc",
             "research_mode": True},
            {"gdpr_preset": ""})
        self.assertEqual(cfg["gdpr_preset"], "")
        self.assertTrue(cfg["research_mode"])


class TestDegradationTally(unittest.TestCase):
    """L7b: _web_gate_audit zählt auf dem Request-Kontext mit — Counts, nie
    Werte. Der Worker draint das Dict in metadata.gdpr_degradation."""

    def test_gate_audit_tallies_by_kind(self):
        with request_context():
            brain._web_gate_audit("searxng_search", ["name"], "refuse",
                                  kind="original")
            brain._web_gate_audit("searxng_search", ["name"], "refuse",
                                  kind="fake")
            brain._web_gate_audit("exa_search", ["dob"], "ask", kind="denied")
            brain._web_gate_audit("web_fetch", ["name"], "ask",
                                  kind="released")
            brain._web_gate_audit("searxng_search", ["name"], "allow",
                                  kind="allowed")
            d = get_request_context()._gdpr_degradation
            self.assertEqual(d, {"web_blocked": 2, "web_denied": 1,
                                 "web_released": 1, "web_allowed": 1})

    def test_tally_never_carries_values(self):
        with request_context():
            brain._web_gate_audit("searxng_search", ["name"], "refuse",
                                  kind="original")
            d = get_request_context()._gdpr_degradation
            self.assertNotIn("Bonnie", json.dumps(d))
            self.assertTrue(all(isinstance(v, int) for v in d.values()))


if __name__ == "__main__":
    unittest.main()
