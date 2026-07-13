"""Unit tests for the spaCy NER PII layer (engine/pii_ner.py) and its
integration into brain._pii_scan_text.

Tests skip cleanly when spaCy or the German model isn't installed, so CI
without the model still passes.

Run with: python3 -m pytest tests/test_pii_ner.py -v
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import pii_ner  # noqa: E402


def _can_load_german() -> bool:
    """True if spaCy + the German model are importable in this environment.
    Used to gate the heavy tests so they degrade cleanly on machines that
    haven't run `pip install -r requirements.txt`."""
    try:
        import spacy  # noqa: F401
    except Exception:
        return False
    try:
        import de_core_news_sm  # type: ignore  # noqa: F401
        return True
    except Exception:
        # Model may still be loadable via spacy.load even without the importable
        # package wheel — try a cheap probe.
        try:
            import spacy
            spacy.util.get_package_path("de_core_news_sm")
            return True
        except Exception:
            return False


GERMAN_AVAILABLE = _can_load_german()


# ──────────────────────────────────────────────────────────────────────────
# Load / availability
# ──────────────────────────────────────────────────────────────────────────


class TestNERLoad(unittest.TestCase):

    @unittest.skipUnless(GERMAN_AVAILABLE, "de_core_news_sm not installed")
    def test_load_german_success(self):
        # Reset module state so we're loading fresh, then restore at end.
        pii_ner._NLP_CACHE.pop("de", None)
        pii_ner._LOAD_FAILED.discard("de")
        try:
            pii_ner.load_models(("de",))
            self.assertTrue(pii_ner.is_available("de"))
        finally:
            pass  # leave loaded — other tests reuse it

    def test_load_unsupported_lang_no_op(self):
        # Made-up language code should mark as failed; subsequent
        # is_available stays False; scan_text returns []. Not a hard error.
        pii_ner._LOAD_FAILED.discard("xx")
        pii_ner._NLP_CACHE.pop("xx", None)
        pii_ner.load_models(("xx",))
        self.assertFalse(pii_ner.is_available("xx"))
        self.assertIn("xx", pii_ner._LOAD_FAILED)
        self.assertEqual(pii_ner.scan_text("Maria Schmidt", lang="xx"), [])

    def test_load_failure_graceful(self):
        # Monkeypatch spacy.load to raise; load_models must log + continue,
        # never re-raise. The language ends up in _LOAD_FAILED.
        pii_ner._LOAD_FAILED.discard("zz")
        pii_ner._NLP_CACHE.pop("zz", None)
        # Need to register a model id for 'zz' so load actually tries.
        with mock.patch.object(pii_ner, "_model_id_for",
                                return_value="de_core_news_sm"):
            with mock.patch("spacy.load", side_effect=RuntimeError("boom")):
                pii_ner.load_models(("zz",))
        self.assertFalse(pii_ner.is_available("zz"))
        self.assertIn("zz", pii_ner._LOAD_FAILED)


# ──────────────────────────────────────────────────────────────────────────
# Scan
# ──────────────────────────────────────────────────────────────────────────


@unittest.skipUnless(GERMAN_AVAILABLE, "de_core_news_sm not installed")
class TestNERScan(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pii_ner.load_models(("de",))
        assert pii_ner.is_available("de"), "German model failed to load"

    def test_person_detected(self):
        text = "Maria Schmidt hat mir gestern die Unterlagen geschickt."
        findings = pii_ner.scan_text(text)
        names = [f for f in findings if f["rule_id"] == "name"]
        self.assertTrue(names, f"no PER entity found in {text!r}: {findings}")
        # Expect at least one finding spanning a chunk of 'Maria Schmidt'
        any_match = any("Maria" in text[f["start"]:f["end"]] for f in names)
        self.assertTrue(any_match, f"PER spans didn't cover 'Maria': {names}")

    def test_location_mapped_to_address(self):
        text = "Ich wohne in München an der Hauptstraße."
        findings = pii_ner.scan_text(text)
        addrs = [f for f in findings if f["rule_id"] == "address"]
        # spaCy small model may or may not catch the street, but should
        # reliably catch München.
        self.assertTrue(addrs, f"no LOC entity found in {text!r}: {findings}")
        self.assertTrue(any("München" in text[f["start"]:f["end"]] for f in addrs))

    def test_organisation_detected(self):
        text = "Sie arbeitet bei Siemens in München."
        findings = pii_ner.scan_text(text)
        orgs = [f for f in findings if f["rule_id"] == "organisation"]
        self.assertTrue(orgs, f"no ORG entity found in {text!r}: {findings}")
        self.assertTrue(any("Siemens" in text[f["start"]:f["end"]] for f in orgs))

    def test_short_entities_filtered(self):
        # _MIN_ENTITY_CHARS=3 — even if spaCy tags 'Du' or 'AB', drop it.
        findings = pii_ner.scan_text("Du AB CD.")
        for f in findings:
            self.assertGreaterEqual(f["end"] - f["start"], 3)

    def test_empty_text_returns_empty(self):
        self.assertEqual(pii_ner.scan_text(""), [])
        self.assertEqual(pii_ner.scan_text(None), [])  # type: ignore[arg-type]

    def test_findings_shape(self):
        findings = pii_ner.scan_text("Maria Schmidt arbeitet bei Siemens.")
        self.assertTrue(findings)
        for f in findings:
            for k in ("rule_id", "label", "category", "start", "end", "len", "source"):
                self.assertIn(k, f, f"missing {k} in {f}")
            # Category mirrors PII_RULE_CATEGORIES: name/address → contact,
            # organisation → business_id (a legal entity is not a natural
            # person; was hardcoded 'contact' until 9.314.2).
            self.assertEqual(f["category"],
                             pii_ner.PII_RULE_CATEGORIES.get(f["rule_id"], "contact"))
            self.assertEqual(f["source"], "ner")
            self.assertEqual(f["len"], f["end"] - f["start"])

    def test_max_findings_caps(self):
        # Long text with many entities — cap should hold.
        text = ("Maria Schmidt. " * 30) + ("Siemens München. " * 30)
        findings = pii_ner.scan_text(text, max_findings=5)
        self.assertLessEqual(len(findings), 5)


# ──────────────────────────────────────────────────────────────────────────
# Shape gate (capitalisation / acronym blocklist)
# ──────────────────────────────────────────────────────────────────────────


class TestShapeGate(unittest.TestCase):
    """The shape gate runs purely on the candidate string + rule_id, so we
    can test it without loading spaCy."""

    def test_lowercase_per_dropped(self):
        # The exact FPs from chat 168fc2d0: function-words + verbs that
        # sm-model tags as PER.
        for s in ("ich wohne", "mein name", "ich heiße", "wohne in"):
            self.assertFalse(pii_ner._passes_shape_gate(s, "name"),
                f"lowercase PER span should be dropped: {s!r}")

    def test_lowercase_loc_dropped(self):
        # The address-side FP from the same chat — "wien" written lowercase.
        for s in ("wien", "münchen", "berlin"):
            self.assertFalse(pii_ner._passes_shape_gate(s, "address"),
                f"lowercase LOC span should be dropped: {s!r}")

    def test_proper_names_kept(self):
        for s in ("Maria Schmidt", "Anna", "Hans-Peter Müller"):
            self.assertTrue(pii_ner._passes_shape_gate(s, "name"),
                f"proper-cased name should pass: {s!r}")

    def test_proper_locations_kept(self):
        for s in ("München", "Berlin", "Hauptstraße"):
            self.assertTrue(pii_ner._passes_shape_gate(s, "address"),
                f"proper-cased location should pass: {s!r}")

    def test_proper_orgs_kept(self):
        for s in ("Siemens", "Deutsche Bahn", "BMW"):
            # BMW is all-caps but not in the acronym blocklist → kept.
            self.assertTrue(pii_ner._passes_shape_gate(s, "organisation"),
                f"proper-cased org should pass: {s!r}")

    def test_acronym_blocklist_orgs_dropped(self):
        # Common legal/technical acronyms that the sm model occasionally
        # mislabels as ORG when used in prose.
        for s in ("DSGVO", "IBAN", "BGB", "EU"):
            self.assertFalse(pii_ner._passes_shape_gate(s, "organisation"),
                f"acronym should be blocked for ORG: {s!r}")

    def test_acronym_blocklist_only_orgs(self):
        # The blocklist is ORG-only — if spaCy mislabels "DSGVO" as PER
        # (unlikely but possible), the cap-gate still drops it (all-caps,
        # but uppercase-start passes), so we just verify the blocklist
        # itself doesn't apply outside ORG.
        self.assertTrue(pii_ner._passes_shape_gate("DSGVO", "name"))

    def test_digits_only_dropped(self):
        # If spaCy ever tags pure digits, drop them — regex catches numbers.
        for s in ("12345", "11", "2026"):
            self.assertFalse(pii_ner._passes_shape_gate(s, "name"))

    def test_empty_dropped(self):
        self.assertFalse(pii_ner._passes_shape_gate("", "name"))


@unittest.skipUnless(GERMAN_AVAILABLE, "de_core_news_sm not installed")
class TestShapeGateIntegration(unittest.TestCase):
    """End-to-end: feed the canonical FP sentences from chat 168fc2d0
    through scan_text and verify the gate suppresses them."""

    @classmethod
    def setUpClass(cls):
        pii_ner.load_models(("de",))

    def test_ich_wohne_lowercase_address_fp_suppressed(self):
        # Verbatim from chat 168fc2d0 turn 2.
        text = "ich wohne in springenfelserbengrund 11, a-1220 wien"
        findings = pii_ner.scan_text(text)
        # All findings should pass the gate. Specifically: no `name` for
        # "ich wohne" and no `address` for "wien".
        for f in findings:
            v = text[f["start"]:f["end"]]
            self.assertFalse(v.lower() == v and v.isalpha() is False or
                             (v == "ich wohne" or v == "wien"),
                f"FP slipped through gate: {f}")

    def test_mein_name_lowercase_fp_suppressed(self):
        # Verbatim from chat 168fc2d0 turn 3 — "mein name" was tagged PER.
        text = "mein name ist Alexander Klinsky"
        findings = pii_ner.scan_text(text)
        for f in findings:
            v = text[f["start"]:f["end"]]
            self.assertNotEqual(v, "mein name",
                f"'mein name' should be dropped by gate, got: {f}")
        # The legitimate name should still come through.
        names = [text[f["start"]:f["end"]] for f in findings
                 if f["rule_id"] == "name"]
        self.assertTrue(any("Alexander" in n for n in names),
            f"legitimate name 'Alexander Klinsky' was lost: {findings}")


# ──────────────────────────────────────────────────────────────────────────
# Integration with brain._pii_scan_text
# ──────────────────────────────────────────────────────────────────────────


@unittest.skipUnless(GERMAN_AVAILABLE, "de_core_news_sm not installed")
class TestNERIntegration(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pii_ner.load_models(("de",))
        # Late import — brain.py is heavy.
        import brain  # noqa: F401
        cls.brain = brain

    def _scan(self, text, **cfg_overrides):
        cfg = self.brain._get_gdpr_scanner_config()
        cfg = {**cfg, **cfg_overrides}
        return self.brain._pii_scan_text(text, cfg=cfg)

    def test_ner_findings_merge_with_regex(self):
        # Forces contact=warn so both regex (email) and NER (name) surface.
        # The default `contact: ignore` is the user-friendly setting; this
        # test pins the merge mechanics independent of the default action.
        text = ("Maria Schmidt hat mir per E-Mail (maria@example.com) gesagt, "
                "dass sie in der Hauptstraße 12 wohnt.")
        findings = self._scan(text, categories={"contact": {"action": "warn"}})
        rule_ids = {f["rule_id"] for f in findings}
        self.assertIn("email", rule_ids, f"email regex didn't fire: {findings}")
        self.assertIn("name", rule_ids, f"NER name didn't fire: {findings}")
        # No double-counting: each char offset belongs to at most one finding.
        spans = sorted((f["start"], f["end"]) for f in findings)
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            self.assertLessEqual(e1, s2,
                f"overlap suppression failed: {spans}")

    def test_unload_silences_ner_findings(self):
        # Unloading the model is the runtime kill-switch for NER (no
        # separate `ner_enabled` config exists). When the cache is empty,
        # `_pii_scan_text` produces zero NER findings even if the contact
        # category is bumped to warn.
        text = "Maria Schmidt arbeitet bei Siemens."
        pii_ner.unload_model("de")
        try:
            findings = self._scan(
                text, categories={"contact": {"action": "warn"}})
            rule_ids = {f["rule_id"] for f in findings}
            self.assertNotIn("name", rule_ids)
            self.assertNotIn("organisation", rule_ids)
        finally:
            pii_ner.load_models(("de",))

    def test_ner_rule_override_ignore(self):
        # rule_overrides.name='ignore' must drop NER name findings even when
        # the surrounding category is bumped to warn. organisation should
        # still surface because only the `name` rule was overridden.
        # NB: `organisation` lives under `business_id` (a legal entity is not
        # a natural person — default-ignore), so that category must be bumped
        # too or the org finding never surfaces regardless of overrides.
        text = "Maria Schmidt arbeitet bei Siemens."
        findings = self._scan(text,
                              categories={"contact": {"action": "warn"},
                                          "business_id": {"action": "warn"}},
                              rule_overrides={"name": "ignore"})
        rule_ids = {f["rule_id"] for f in findings}
        self.assertNotIn("name", rule_ids)
        self.assertIn("organisation", rule_ids,
            f"non-overridden NER rule got dropped: {findings}")

    def test_action_resolves_from_contact_category(self):
        # NER rule_ids live under the `contact` category alongside email/phone.
        # The category default in PII_DEFAULT_CATEGORY_ACTIONS is `ignore`; if
        # the live config.json has bumped it to warn/block, NER findings still
        # inherit that action. Test against the default explicitly so this
        # doesn't depend on the developer's saved config.
        text = "Maria Schmidt arbeitet bei Siemens."
        findings = self._scan(text, categories={"contact": {"action": "ignore"}})
        ner_rids = {"name", "address", "organisation"}
        ner_findings = [f for f in findings if f["rule_id"] in ner_rids]
        self.assertEqual(ner_findings, [],
            f"NER findings should be ignored when contact=ignore: "
            f"{ner_findings}")

    def test_contact_warn_promotes_ner_findings(self):
        # Flipping the contact category to warn surfaces NER findings.
        text = "Maria Schmidt arbeitet bei Siemens."
        findings = self._scan(text, categories={"contact": {"action": "warn"}})
        rule_ids = {f["rule_id"] for f in findings}
        self.assertIn("name", rule_ids,
            f"NER name didn't surface when contact=warn: {findings}")
        for f in findings:
            if f["rule_id"] in ("name", "address", "organisation"):
                self.assertEqual(f["action"], "warn")


# ──────────────────────────────────────────────────────────────────────────
# Pseudonymizer round-trip — NER findings must mint sensible tokens.
# ──────────────────────────────────────────────────────────────────────────


@unittest.skipUnless(GERMAN_AVAILABLE, "de_core_news_sm not installed")
class TestPseudonymizerRoundtrip(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        pii_ner.load_models(("de",))
        import brain  # noqa
        cls.brain = brain
        import pseudonymizer
        cls.ps = pseudonymizer

    def test_name_roundtrip(self):
        original = "Maria Schmidt arbeitet bei Siemens in München."
        # name lives under `contact`, organisation under `business_id` (a
        # legal entity is not a natural person) — both default to ignore, so
        # bump both to get real findings to pseudonymise. rule_overrides is
        # pinned EMPTY so the developer's live config.json (which may set
        # organisation=ignore) can't decide whether this test passes.
        cfg = {**self.brain._get_gdpr_scanner_config(),
               "categories": {"contact": {"action": "warn"},
                              "business_id": {"action": "warn"}},
               "rule_overrides": {}}
        findings = self.brain._pii_scan_text(original, cfg=cfg)
        # Need at least one of each rule_id to make this test meaningful.
        rule_ids = {f["rule_id"] for f in findings}
        self.assertTrue({"name", "organisation"}.issubset(rule_ids),
            f"NER didn't produce name+organisation: {rule_ids}")
        mapping = self.ps.new_mapping()
        try:
            anon = self.ps.pseudonymize_text(original, findings, mapping=mapping)
            # Original PII must be gone from the wire copy.
            self.assertNotIn("Maria Schmidt", anon)
            self.assertNotIn("Siemens", anon)
            # names/orgs get REALISTIC surrogates ("Maria Parker", "Hooli
            # Corp"), not <NAME_N> tokens — the wire copy must read as natural
            # text to the cloud model. The invariant is the MAPPING, not a
            # token shape: one forward entry per distinct original, reversible.
            self.assertGreaterEqual(len(mapping.forward), 2,
                f"expected mapping entries for name+organisation: {mapping.forward}")
            # Reverse restores verbatim.
            restored, n = self.ps.deanonymize_text(anon, mapping=mapping)
            self.assertEqual(restored, original)
            self.assertGreater(n, 0)
        finally:
            self.ps.close_mapping(mapping.mapping_id)


if __name__ == "__main__":
    unittest.main()
