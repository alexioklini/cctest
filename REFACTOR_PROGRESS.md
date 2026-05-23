# Refactor Progress Ledger

Running record of the module-extraction refactor. **This file is the source of truth for
"what's done" — read it first on resume.** Updated after every extraction (disk = memory,
so the run survives context compaction and fresh sessions). Protocol: see `REFACTOR_HANDOVER.md`
→ *Execution protocol*. Plan: `REFACTOR_PLAN.md`.

**Autonomy:** auto through Phase 3 (Tier A + B + splits); HARD STOP before Tier C.

---

## Status board

| Phase | Scope | State |
|---|---|---|
| 0 | Safety net (gate + baseline) | ✅ DONE (commit `d48b5de`) |
| 1 | Tier-D audit + Tier A pure wins + admin/workflows + db splits | ⬜ not started |
| 2 | B1 `engine/context.py` (relocate only, NOT DI) + U1/U2/U4 utilities | ⬜ not started |
| 3 | B2 scheduler (⚠️ chars-tests first) · B3 PII(+U5) · B4 quotas · full admin/ split · server_daemons (⚠️ daemons nested in main()) · chat.py split | ⬜ not started |
| 4 | Tier C (C1/C2/C3, ⚠️ chars-tests + eval before C2) + finish D1–D3 | ⛔ STOP — needs user review before starting |

**⚠️ markers** = a characterization test must be written+committed for that path BEFORE the extraction (plan §1.5). Core paths have no existing tests, so the gate alone can't catch regressions there.

---

## Extraction ledger

One row per extraction. Append on each green commit. Symbol = the marker used for the Gate-2 grep.

| # | Extraction | Symbol | Target module | Commit | Gate | Notes |
|---|---|---|---|---|---|---|
| — | (none yet) | — | — | — | — | Phase 0 only established the safety net |

---

## Blockers / decisions encountered mid-run

(empty — log here if a gate fails or an unforeseen decision arises, then STOP and report)

---

## Baseline (from Phase 0, for gate comparison)
- Imports: **18/18 clean** on `/opt/homebrew/bin/python3`.
- Tests: **80 pass / 3 fail**; the 3 are `test_contact_warn_promotes_ner_findings`, `test_name_roundtrip`, `test_ner_findings_merge_with_regex` (all NER-env, not code). Gate rule = no NEW failures beyond these.
- `./refactor_gate.sh` → **GATE PASS ✓** at clean HEAD.
