# Refactor Progress Report

Living record of the module-extraction refactor. **This file is the source of truth for
"what's done" — read it first on resume.** Updated after every extraction (disk = memory,
so the run survives context compaction and fresh sessions). Protocol: see `REFACTOR_HANDOVER.md`
→ *Execution protocol*. Plan: `REFACTOR_PLAN.md`.

**Autonomy:** auto through Phase 3 (Tier A + B + splits); HARD STOP before Tier C.

**Governing principles (user, override all else):** (1) split monolith into clear functional domains; (2) net duplication zero & trending down — a half-done move is worse than none, so don't start what can't be finished cleanly; (3) **DONE = original code GONE, logic lives in exactly one place.** Gate 2 enforces #3 mechanically: a surviving `def`/`class` in brain.py = FAIL → finish or revert. One extraction = one atomic commit (old gone + new arrives together).

> **Reporting rule for the autonomous run:** after each extraction, append a full row to *Extraction record* below AND flip the *Status board*. Record source→destination, whether the old code was deleted (the principle-#3 evidence), and the gate/test result — green or not. A reverted/abandoned attempt is logged too (state = REVERTED), so the report shows what was tried, not just what stuck. The report is updated in the SAME commit as the extraction (or immediately after), never deferred.

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

### Running totals
- Extractions completed: **0**
- `brain.py` line count: **25,182** (baseline) → _current: 25,182_
- Net new modules created: **0**
- Live duplicate definitions (brain.py ∩ engine/): **0** (verified 2026-05-23)

---

## Extraction record

One block per extraction, newest first. Every block answers the four questions: **what moved · where to · old code deleted? · tests pass?** Fill every field — "did the old code get deleted" is the principle-#3 acceptance evidence; "tests" is the Gate 4/5 result.

**Template (copy for each new extraction):**
```
### <#> <name> — <state: DONE | REVERTED | BLOCKED>
- **Commit:** <sha>  ·  **Date:** <ISO>  ·  **Phase:** <n>
- **Symbol(s):** <the def/class names moved — used for Gate-2 grep>
- **Moved FROM:** brain.py:<line-range> (<what it was>)
- **Moved TO:** <new module path> (<lines>)
- **Old code deleted?** YES — Gate-2 `./refactor_gate.sh grep <symbol>` shows no def/class in brain.py (alias import only) | NO → see Blockers
- **Callers re-pointed:** <N> sites → <how they now resolve> (Gate 3)
- **Tests:** Gate 4 imports <X/X clean> · Gate 5 unittest <P pass / F fail, only the 3 known NER-env> · gate verdict <PASS/FAIL>
- **Characterization test added?** <n/a (Tier A) | name of test file+case, if B2/C2/etc.>
- **brain.py delta:** <before> → <after> lines (−<N>)
- **Notes:** <anything non-obvious; behavior intentionally changed?>
```

---

_No extractions yet — Phase 0 only established the safety net (gate + baseline). First entry will be the Phase-1 Tier-D audit / first Tier-A extraction._

<!-- WORKED EXAMPLE of a completed entry (delete or keep as format reference):
### 1 A1 workflow engine — DONE
- **Commit:** abc1234  ·  **Date:** 2026-05-24  ·  **Phase:** 1
- **Symbol(s):** _wf_parse, _wf_tok_line, _WFProgram, WorkflowInterpreter
- **Moved FROM:** brain.py:12522–14000 (workflow lexer→AST→interpreter)
- **Moved TO:** engine/workflow.py (1478 lines)
- **Old code deleted?** YES — Gate-2 grep shows only `from engine.workflow import ...` alias in brain.py, no def/class
- **Callers re-pointed:** 5 sites (all inside brain.py) → resolve via the alias import
- **Tests:** Gate 4 imports 18/18 clean · Gate 5 80 pass / 3 fail (known NER) · gate verdict PASS
- **Characterization test added?** n/a (Tier A, self-contained)
- **brain.py delta:** 25,182 → 23,704 lines (−1,478)
- **Notes:** pure relocation, zero behavior change.
-->

---

## Blockers / decisions encountered mid-run

(empty — log here if a gate fails, an extraction is reverted, or an unforeseen decision arises; then STOP and report. Each entry: what was attempted, what failed, what state the tree is in now.)

---

## Baseline (from Phase 0, for gate comparison)
- Imports: **18/18 clean** on `/opt/homebrew/bin/python3`.
- Tests: **80 pass / 3 fail**; the 3 are `test_contact_warn_promotes_ner_findings`, `test_name_roundtrip`, `test_ner_findings_merge_with_regex` (all NER-env, not code). Gate rule = no NEW failures beyond these.
- `./refactor_gate.sh` → **GATE PASS ✓** at clean HEAD `4bad7e4`.
