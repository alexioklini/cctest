---
name: project_kg_gdpr_anonymise_incident
description: "ROOT CAUSE (corrected) of the 2026-06 policy-KG=0 incident: GDPR anonymise (date rule) mangled policy chunks → model returned empty. NOT a provider/model bug. GDPR now disabled."
metadata: 
  node_type: memory
  type: project
  originSessionId: 873784fc-1686-4cf4-9a18-970747445214
---

2026-06-07: The policy project's KG dropped to ~0 triples. After THREE wrong theories (dead provider / retired model snapshot / evals-don't-use-KG — all disproven), the REAL root cause, proven by reproduction:

**The GDPR `anonymise` policy mangled the policy-document chunks before KG extraction.** Chain: policy PDFs (German bank ARLs) are saturated with DATES (version/effective/approval dates) → the `date` PII detector (category `personal`) fired **215×** across the 53 docs (by far the dominant rule; next: ipv4 23, email 19, phone 12) → `gdpr_scanner.background_pii_action: anonymise` pseudonymised every date → the gutted chunk (`[gdpr] ... purpose=kg_extract tokens=1`) went to the extraction model → model returned empty → `_progress_record(..., "sidecar returned no reply")`. NOT a provider/model/transport failure — `mistral-small-2603` on mistral-direct responded fine the whole time; the `mistral-experimental/` prefix in extraction_model was cosmetic (resolved via the model's real `mistral-direct` provider).

**Two amplifiers fixed (committed 5a728be):** (1) failed extraction STILL advanced the progress cursor → permanent skip → fixed: real failures no longer record progress (retry next cycle), both per-chunk + per-drawer branches. (2) errors buried in daemon log → added loud "EXTRACTION MODEL APPEARS BROKEN" line.

**RESOLUTION (per user 2026-06-07): ALL GDPR + classification DISABLED** — `gdpr_scanner.enabled=false` + `classification_scanner.enabled=false` in config.json (both have clean early-return-unchanged gates). No anonymise, no block, no force-local-fallback, for interactive chat AND background. Policy KG recovered to 1,470 triples extracting cleanly after disable. config.json.bak-gdpr-disable kept.

**Planned (not yet built):** even with GDPR on, KG mining should SKIP a doc GDPR would block/anonymise (don't mangle-then-extract garbage) + show per-doc state in project view (mined+kg / mined / not mined / error / kg-skipped) with tooltip. The `date`-rule-as-personal-PII is over-broad for document mining (dates in policies aren't sensitive) — candidate to set `date`/`personal`→ignore if GDPR is re-enabled.

LESSON: I asserted root cause 3× from log-reading before reproducing. Reproduce first. The user was right each time. Related: [[project_devbox_sqlite_exact_switch]], [[project_gdpr_granular_config]], [[project_classification_phase_b]].
