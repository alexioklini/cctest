# Refactor Progress Report

Living record of the module-extraction refactor. **This file is the source of truth for
"what's done" — read it first on resume.** Updated after every extraction (disk = memory,
so the run survives context compaction and fresh sessions). Protocol: see `REFACTOR_HANDOVER.md`
→ *Execution protocol*. Plan: `REFACTOR_PLAN.md`.

**Autonomy:** auto through Phase 3 (Tier A + B + splits); HARD STOP before Tier C.

**Governing principles (user, override all else):** (1) split monolith into clear functional domains; (2) net duplication zero & trending down — a half-done move is worse than none, so don't start what can't be finished cleanly; (3) **DONE = original code GONE, logic lives in exactly one place.** Gate 2 enforces #3 mechanically: a surviving `def`/`class` in brain.py = FAIL → finish or revert. One extraction = one atomic commit (old gone + new arrives together).

> **Reporting rule for the autonomous run:** after each extraction, (1) append a full block to *Extraction record* below, (2) flip the domain's row in the *Master domain map* (⬜→🔄→✅), and (3) flip the *Status board* + *Running totals*. Record source→destination, whether the old code was deleted (the principle-#3 evidence), and the gate/test result — green or not. A reverted/abandoned attempt is logged too (state = REVERTED), so the report shows what was tried, not just what stuck. The report is updated in the SAME commit as the extraction (or immediately after), never deferred. **The Master domain map is the complete scope from day one — never add a domain to it as "newly discovered work"; if something genuinely new appears, that's a scope change to flag, not a silent append.**

---

## Status board

| Phase | Scope | State |
|---|---|---|
| 0 | Safety net (gate + baseline) | ✅ DONE (commit `d48b5de`) |
| 1 | Tier-D audit + Tier A pure wins + admin/workflows + db splits | 🔄 in progress (D-audit done; D2 ✅) |
| 2 | B1 `engine/context.py` (relocate only, NOT DI) + U1/U2/U4 utilities | ⬜ not started |
| 3 | B2 scheduler (⚠️ chars-tests first) · B3 PII(+U5) · B4 quotas · full admin/ split · server_daemons (⚠️ daemons nested in main()) · chat.py split | ⬜ not started |
| 4 | Tier C (C1/C2/C3, ⚠️ chars-tests + eval before C2) + finish D1–D3 | ⛔ STOP — needs user review before starting |

**⚠️ markers** = a characterization test must be written+committed for that path BEFORE the extraction (plan §1.5). Core paths have no existing tests, so the gate alone can't catch regressions there.

---

## Master domain map — the COMPLETE scope (planned + done + excluded)

Every functional domain the full refactor touches is listed here from day one — not grown as work proceeds. Each row carries its **phase**, **target module**, and **status**. Domains not yet touched say so and name the phase that will cover them. Domains that will **NOT** be touched are listed with the reason. Status legend: ✅ done · ⬜ planned (not started) · 🔄 in progress · ⛔ gated (needs review) · 🚫 out of scope.

### A. `brain.py` (25,182 LOC) — domains to extract

| Domain | Source (brain.py) | → Target module | Phase | Status | Note |
|---|---|---|---|---|---|
| Workflow engine (lexer→AST→interpreter) | ~12,522–14,000 | `engine/workflow.py` | 1 (Tier A) | ⬜ planned | self-contained; first pure win |
| Code structure graph (tree-sitter, code-graph.db) | ~17,779–18,952 | `engine/code_graph.py` | 1 (Tier A) | ⬜ planned | owns its DB pool |
| Git / GitHub tools | 18,978 / 19,176 | `engine/tools/git_tools.py` | 1 (Tier A) | ⬜ planned | subprocess wrappers; keep 4-edit-site registration in brain.py |
| Gmail tools | ~4,916–5,200 | `engine/tools/gmail_tools.py` | 1 (Tier A) | ⬜ planned | API-client tools |
| Trace manager + audit trail | ~16,365–16,762 | `server_lib/trace_audit.py` | 1 (Tier A) | ⬜ planned | owns traces/audit DB pools |
| `_thread_local` + execution context | ~11,257+ | `engine/context.py` | 2 (B1) | ⬜ planned | **relocate only**, NOT DI; prerequisite for B2–B4 |
| Scheduler + task runner | ~14,000–15,641 | `engine/scheduler.py` | 3 (B2) | ⬜ planned | ⚠️ characterization test first; coupled to `_thread_local` |
| GDPR/PII scanner (`_pii_rules`/`_pii_scan_*`) | 16,771–17,477 | `engine/pii_scan.py` (merge into `engine/pii_ner.py`) | 3 (B3) | ⬜ planned | fixes incoherent split (logic in brain, loader in engine) + web/index.html sync via U5 |
| Quotas / cost / rate-limit (`QuotaManager`/`CostTracker`/`RateLimiter`) | scattered | `engine/quotas.py`, `engine/cost.py` | 3 (B4) | ⬜ planned | each owns a DB pool |
| Model selection + system-prompt assembly (`_build_system_prompt`, `MODEL_PROFILES`) | ~21,844–24,482 | `engine/prompt_build.py`, `engine/model_select.py` | 4 (C1) | ⛔ gated | KV-cache sensitive; eval + warmup byte-stability gate |
| Tool execution layer (artifact-session, dedup, summarization) | ~2,839–4,845 | `engine/tool_exec.py` | 4 (C2) | ⛔ gated | ⚠️ characterization test first; core path |
| MemPalace integration glue (`tool_mempalace_query`, wing resolution) | ~5,386+ | `engine/mempalace_glue.py` | 4 (C3) | ⛔ gated | wing-isolation test gate (security) |
| **D1** doc_convert inline remnants | tool_read_document etc. | `engine/doc_convert.py` (already exists) | 1 audit | ✅ clean | audit 2026-05-23: `convert_one`/`_extract_pdf`/`_do_extract` already only in engine; no duplicate — nothing to do |
| **D2** classification enforcement glue (`_classification_gate_tool_text` etc.) | 2,892 / 20,836 / 20,892 | `engine/classification.py` | 1 | ✅ done | commit `29b142b`; 3 fns moved next to detector, brain re-exports via alias |
| **D3** KG entity-indexing + co-occurrence | ~10,279–10,450 | `engine/kg_extract.py` (already exists) | 1 audit | ✅ clean | audit 2026-05-23: entity-index/co-occurrence is distinct from kg_extract's triple extraction; correctly stays in brain.py, no duplicate |

### B. Other oversized files — domains to split

| Domain | Source | → Target | Phase | Status | Note |
|---|---|---|---|---|---|
| admin: workflows | `handlers/admin.py` ~219–1,140 | `handlers/admin/workflows.py` | 1 | ⬜ planned | isolated; extract first |
| admin: artifacts/files/sidecar/channels | admin.py ~2,200–5,416 | `handlers/admin/artifacts.py` | 3 | ⬜ planned | largest cluster (~3,200) |
| admin: costs/quotas UI | admin.py ~1,610–2,100 | `handlers/admin/costs.py` | 3 | ⬜ planned | |
| admin: skills | admin.py ~1,305–1,610 | `handlers/admin/skills.py` | 3 | ⬜ planned | |
| admin: tool-settings/research/NER config | admin.py ~1,707–2,000 | `handlers/admin/config.py` | 3 | ⬜ planned | |
| admin: teams | admin.py ~19–220 | `handlers/admin/teams.py` | 3 | ⬜ planned | |
| admin: agents | admin.py ~1,210–1,392 | `handlers/admin/agents.py` | 3 | ⬜ planned | |
| admin: KG/traces/audit observability | admin.py ~2,099–2,200 | `handlers/admin/observability.py` | 3 | ⬜ planned | |
| server: 7 background daemons (nested in `main()`) | `server.py` ~3,903–5,716 | `server_daemons.py` | 3 | ⬜ planned | ⚠️ lift-to-module-scope, not copy-paste |
| server: MemPalaceClient singleton | `server.py:69` | `server_lib/mempalace_client.py` | 3 | ⬜ planned | flagged by external analysis |
| server: bootstrap/init (optional) | `server.py` ~3,033–3,500 | `server_init.py` | 3 | ⬜ planned | optional; `main()` may stay |
| chat: SSE streaming (format/keepalive/replay) | `handlers/chat.py` | `server_lib/sse_stream.py` | 3 | ⬜ planned | reusable by future SSE endpoints (folds U3) |
| chat: GDPR-recovery modal state machine | `handlers/chat.py` ~51–200 | `handlers/gdpr_recovery.py` | 3 | ⬜ planned | |
| db: node registry | `server_lib/db.py` ~54–130 | `server_lib/node_registry.py` | 1 | ⬜ planned | |
| db: MemPalace sync cursor | `server_lib/db.py` ~1,750+ | `server_lib/mempalace_sync.py` | 1 | ⬜ planned | (`ChatDB` core stays in db.py) |

### C. Cross-cutting reusable utilities (de-duplication)

| Utility | Copies today | → Target | Phase | Status | Note |
|---|---|---|---|---|---|
| U1 path-traversal guard | 5 divergent (classification, projects ×2, favourites, admin) | `server_lib/pathsafe.py` | 2 | ⬜ planned | security; HIGH value |
| U2 HTTP body read | 16 sites | `server_lib/http_util.py` | 2 | ⬜ planned | inconsistent error handling today |
| U3 SSE formatter | 3 sites | folded into `server_lib/sse_stream.py` | 3 | ⬜ planned | with chat SSE split |
| U4 repo-root path constant | ~82 sites | `common.py` constant | 2 | ⬜ planned | cosmetic |
| U5 PII web/server rule sync | brain.py ↔ web/index.html | codegen JS table from Python | 3 | ⬜ planned | after B3; stops hand-sync drift |

### D. Domains that will NOT be touched (out of scope) — with reason

| Domain | Status | Why excluded |
|---|---|---|
| Web frontend (~29k LOC vanilla JS: settings.js 6.1k, panels.js 5.8k, chat.js 4k) | 🚫 out of scope | Different language/toolchain/risk profile. Separate future initiative (plan §8). *Exception:* U5 touches `web/index.html`'s PIIScanner via codegen — the one frontend seam crossed. |
| Thread-local → full dependency-injection conversion | 🚫 out of scope | Multi-week architectural change across the whole codebase. B1 relocates only. Could be piloted later (plan §8). |
| Encoding all prose invariants as tests/types | 🚫 out of scope | Broad initiative; §1.5 characterization tests are a first down-payment, not the whole thing. |
| Already-centralized helpers (`_send_json`/`_read_json`, auth gates, `resolve_provider_for_model`, `@_db_safe`/`_db_conn`, TOOL_DEFINITIONS dedup) | 🚫 not needed | Already single-sourced (v8.26.0/v8.28.0). Re-extracting would add churn for zero gain. |
| `handlers/favourites.py` vs `server_lib/favourites.py` | 🚫 not needed | Legitimate HTTP-layer vs DB-layer split, not duplication. |
| `ChatDB` core (stays in `server_lib/db.py`) | 🚫 stays put | The core session store; only node-registry + mempalace-sync peel off around it. |
| Session/SessionManager/LiveStream (stays in `server.py`) | 🚫 stays put | Core abstraction; dispatch layer legitimately lives with the server. |
| `web/index.html` PIIScanner (stays, regex-only) | 🚫 stays (managed) | Browser can't run the Python NER; intentionally regex-only. U5 makes it codegen-synced so it can't drift — but it is not "extracted." |
| `engine/file_pseudonymize.py` | ✅ keep (live) | Audit 2026-05-23: actively imported (pseudonymizer.py re-exports `deanonymize_file`; handlers/chat.py uses `SUPPORTED_EXTS`). NOT dead — leave as-is. |

> **Coverage promise:** every domain above is accounted for — done, planned-with-phase, gated, or excluded-with-reason. If a domain isn't in this table, it's an omission to fix, not silent scope.

### Running totals
- Extractions completed: **1** (D2)
- `brain.py` line count: **25,182** (baseline) → _current: 24,925_ (−257)
- Net new modules created: **0** (D2 merged into existing engine/classification.py)
- Live duplicate definitions (brain.py ∩ engine/): **0** — D2 audit found 3 stranded classification fns, now extracted; D1/D3 confirmed already clean

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

### 1 D2 classification enforcement glue — DONE
- **Commit:** `29b142b`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (Tier-D audit/debt-paydown)
- **Symbol(s):** `_classification_gate_tool_text`, `_classification_effective_action`, `classification_pick_model_for_background`
- **Moved FROM:** brain.py:2892–2971 + 20836–20857 + 20892–21063 (classification enforcement glue, stranded next to GDPR seam — a half-done prior migration; handlers/chat.py already imported `_classification_effective_action` from engine)
- **Moved TO:** engine/classification.py:557–793 (appended after `detect_with_pii`, beside the `detect_classification` detector)
- **Old code deleted?** YES — Gate-2 `./refactor_gate.sh grep <sym>` shows "no definition in brain.py" for all 3; brain.py keeps a single alias re-export `from engine.classification import ...`.
- **Callers re-pointed:** brain.py 2 sites (`_gdpr_anon_tool_text`, `gdpr_pick_model_for_background`) resolve via alias; handlers/chat.py:3415 (`engine._classification_effective_action`) resolves via the `import brain as engine` alias — unchanged.
- **Tests:** Gate 4 imports 18/18 clean · Gate 5 80 pass / 3 fail (only the 3 known NER-env) · gate verdict PASS
- **Characterization test added?** n/a (Tier-D debt-paydown into existing module; not a core path — covered by existing gdpr/classification unit tests)
- **brain.py delta:** 25,182 → 24,925 lines (−257)
- **Notes:** No import cycle — the 3 fns reach mutable brain globals (`_thread_local`, `_audit_log`, etc.) via call-time `import brain as _brain`, the same lazy pattern `detect_with_pii` already used. `ClassificationBlockedError` (subclasses GDPRBlockedError, caught at 10+ background sites) + `_CLASSIFICATION_DEFAULTS` (used by handlers/classification.py) intentionally STAY in brain.py. Pure relocation, zero behavior change (runtime-verified strict→block, public→ignore).

**Audit findings (Phase-1 Tier-D sweep, 2026-05-23 — no code change, recorded for completeness):**
- **D1 doc_convert:** CLEAN. `convert_one`/`_extract_pdf`/`_do_extract` exist only in engine/doc_convert.py — no surviving duplicate in brain.py. v9.10.0 unification was complete.
- **D3 KG entity-indexing:** CLEAN. brain.py:10279–10450 (`_extract_entities`, `_rebuild_entity_index`, `_recall_cooccurrence`, …) is entity co-occurrence + file-linking, a DIFFERENT concern from engine/kg_extract.py's LLM normative-triple extraction. No duplication; correctly stays in brain.py.
- **engine/file_pseudonymize.py:** LIVE, not dead. Imported by pseudonymizer.py + handlers/chat.py.

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
