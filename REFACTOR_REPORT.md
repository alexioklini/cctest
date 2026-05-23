# Refactor Progress Report

Living record of the module-extraction refactor. **This file is the source of truth for
"what's done" — read it first on resume.** Updated after every extraction (disk = memory,
so the run survives context compaction and fresh sessions). Protocol: see `REFACTOR_HANDOVER.md`
→ *Execution protocol*. Plan: `REFACTOR_PLAN.md`.

**Autonomy:** auto through Phase 3 (Tier A + B + splits); HARD STOP before Tier C.

> **🔄 STATUS 2026-05-23: Phases 1–4 DONE (20 extractions, 0 reverts). Tier E ADDED (user request) — IN PROGRESS.** Tiers A–D + Tier C complete: brain.py 25,182 → 16,950 (−32.7%); handlers/admin.py 5,416 → 79 (−98.5%); server.py 5,827 → 3,895 (−33%). A source-vs-report audit (2026-05-23) verified the refactor faithful (0 surviving duplicate defs, all 24 modules real, re-exports resolve to new modules). **Tier E** (data-registry + ingest pipeline + the remaining ~48 `tool_*` implementations) is now planned + in progress — target brain.py ≈13–13.5k LOC. See *Tier E plan* + *Status board*.

**Governing principles (user, override all else):** (1) split monolith into clear functional domains; (2) net duplication zero & trending down — a half-done move is worse than none, so don't start what can't be finished cleanly; (3) **DONE = original code GONE, logic lives in exactly one place.** Gate 2 enforces #3 mechanically: a surviving `def`/`class` in brain.py = FAIL → finish or revert. One extraction = one atomic commit (old gone + new arrives together).

> **Reporting rule for the autonomous run:** after each extraction, (1) append a full block to *Extraction record* below, (2) flip the domain's row in the *Master domain map* (⬜→🔄→✅), and (3) flip the *Status board* + *Running totals*. Record source→destination, whether the old code was deleted (the principle-#3 evidence), and the gate/test result — green or not. A reverted/abandoned attempt is logged too (state = REVERTED), so the report shows what was tried, not just what stuck. The report is updated in the SAME commit as the extraction (or immediately after), never deferred. **The Master domain map is the complete scope from day one — never add a domain to it as "newly discovered work"; if something genuinely new appears, that's a scope change to flag, not a silent append.**

---

## Status board

| Phase | Scope | State |
|---|---|---|
| 0 | Safety net (gate + baseline) | ✅ DONE (commit `d48b5de`) |
| 1 | Tier-D audit + Tier A pure wins + admin/workflows + db splits | ✅ DONE — D-audit (D1/D3 clean), D2, A1–A5, db node-registry+mempalace-sync, admin workflows. brain.py −3,420 |
| 2 | B1 `engine/context.py` (relocate only, NOT DI) + U1/U2/U4 utilities | ✅ DONE — B1 ✅, U1 ✅(partial), U2 ✅(already-satisfied), U4 🚫 SKIP(not-applicable) |
| 3 | B2 scheduler (⚠️ chars-tests first) · B3 PII(+U5) · B4 quotas · full admin/ split · server_daemons (⚠️ daemons nested in main()) · chat.py split | ✅ DONE — B2✅ B3✅ B4✅ admin-full✅ server_daemons✅ chat-split✅ (+U3✅). MemPalaceClient + server_init deferred/optional |
| 4 | Tier C (C1/C2/C3, ⚠️ chars-tests + eval before C2) + finish D1–D3 | ✅ **DONE — C1 ✅ (`f83e72e`, warmup byte-identical, eval Δ−0.06). C2 ✅ (`9c9bc57`, chars-test 27 cases, eval Δ−0.02). C3 ✅ (`100bba2`, wing-isolation 9/9, eval confirm-run 0.79 ≈ baseline). D1/D2/D3 verified final (no duplication). ALL TIER C COMPLETE.** |
| 5 | **Tier E — registry-data + ingest pipeline + remaining tool_* implementations** | ⬜ **PLANNED (added 2026-05-23 by user request).** E1 file/shell/python/docs tools (⚠️ chars-test first — `_after_file_write`/GDPR coupling). E2 `TOOL_DEFINITIONS` data → `engine/tool_schemas.py` (~1,212 LOC, pure data, biggest single block). E3 document/ingest pipeline → `engine/ingest.py` (~700 LOC). E4 web/image + ask_* + transcribe/translate + delegation/context/skills/nodes/MCP/worker tool bodies → grouped `engine/tools/*.py`. See *Tier E plan* below. Gate per extraction = `./refactor_gate.sh` + (E1) chars-test + eval Δ<0.10. |

**⚠️ markers** = a characterization test must be written+committed for that path BEFORE the extraction (plan §1.5). Core paths have no existing tests, so the gate alone can't catch regressions there.

---

## Master domain map — the COMPLETE scope (planned + done + excluded)

Every functional domain the full refactor touches is listed here from day one — not grown as work proceeds. Each row carries its **phase**, **target module**, and **status**. Domains not yet touched say so and name the phase that will cover them. Domains that will **NOT** be touched are listed with the reason. Status legend: ✅ done · ⬜ planned (not started) · 🔄 in progress · ⛔ gated (needs review) · 🚫 out of scope.

### A. `brain.py` (25,182 LOC) — domains to extract

| Domain | Source (brain.py) | → Target module | Phase | Status | Note |
|---|---|---|---|---|---|
| Workflow engine (lexer→AST→interpreter) | 12,486–13,443 | `engine/workflow.py` | 1 (Tier A) | ✅ done | commit `094ec90`; 977-line new module. Orchestration layer (WorkflowEngine/Execution) stays in brain (runtime-entangled), reaches engine via alias |
| Code structure graph (tree-sitter, code-graph.db) | 16,761–17,931 | `engine/code_graph.py` | 1 (Tier A) | ✅ done | commit `3aa1cf2`; 1205-line new module, owns its DB pool (verified not shared); 4-site tool reg verified |
| Git / GitHub tools | 16,783–17,165 | `engine/tools/git_tools.py` | 1 (Tier A) | ✅ done | commit `3563081`; 4-site reg verified, dispatch-identity True/True |
| Gmail tools | 4,770–5,086 | `engine/tools/gmail_tools.py` | 1 (Tier A) | ✅ done | commit `f8f3a1e`; 5 tools, 4-site reg verified, all dispatch-identity True |
| Trace manager + audit trail | 15,043–15,437 | `server_lib/trace_audit.py` | 1 (Tier A) | ✅ done | commit `fa146c3`; both DB pools moved; 58 `_audit_log` sites resolve via re-export; server.py rebind verified |
| `_thread_local` + execution context | brain.py:10,878 | `engine/context.py` | 2 (B1) | ✅ done | commit `5e56783`; relocated only (not DI); instance identity verified True across 291 sites |
| Scheduler + task runner | 12,950–15,641 | `engine/scheduler.py` | 3 (B2) | ✅ done | commit `2ba75be` (test `b09c5dd` first); 1407-LOC module; _thread_local via engine.context (3-way identity True); invariant #5 preserved; 18/18 chars-tests pass |
| GDPR/PII scanner (`_pii_rules`/`_pii_scan_*`) | (post-shift) | merged into `engine/pii_ner.py` | 3 (B3) | ✅ done | commit `793ca1e`; merged with NER half; rule order preserved; 41/41 GDPR+pseudonymizer tests pass; U5 drift-checker shipped |
| Quotas / cost / rate-limit (`QuotaManager`/`CostTracker`/`RateLimiter`) | scattered | `engine/quotas.py` (single, not split) | 3 (B4) | ✅ done | commit `12127c1`; cohesive single module; costs.db pool moved; _log_call_cost stays in brain; singletons via alias |
| Model selection + system-prompt assembly (`_build_system_prompt`, `MODEL_PROFILES`) | ~21,844–24,482 | `engine/prompt_build.py`, `engine/model_select.py` | 4 (C1) | ✅ done | commit `f83e72e`; warmup prefix BYTE-IDENTICAL (sha b89c5a14, 3357B, 24 tools); eval brain 0.77→0.71 (Δ−0.06, within gate+noise); −952 LOC |
| Tool execution layer (artifact-session, dedup, summarization) | scattered (2841 / 3064 / 16646–16850 / 17836–18070) | `engine/tool_exec.py` | 4 (C2) | ✅ done | commit `9c9bc57` (chars-test `3f87889` first); chars-tests 45/45; eval brain 0.77→0.75 (Δ−0.02); −306 LOC |
| MemPalace integration glue (`tool_mempalace_query`, wing resolution) | ~4,864–5,426 / 5,798 | `engine/mempalace_glue.py` | 4 (C3) | ✅ done | commit `100bba2` (test `3b2115d` first); wing-isolation 9/9 (security); eval brain 0.65 then **0.79** confirm re-run (variance, not regression — in-process retrieval verified working); −606 LOC |
| **D1** doc_convert inline remnants | tool_read_document etc. | `engine/doc_convert.py` (already exists) | 1 audit | ✅ clean | audit 2026-05-23: `convert_one`/`_extract_pdf`/`_do_extract` already only in engine; no duplicate — nothing to do |
| **D2** classification enforcement glue (`_classification_gate_tool_text` etc.) | 2,892 / 20,836 / 20,892 | `engine/classification.py` | 1 | ✅ done | commit `29b142b`; 3 fns moved next to detector, brain re-exports via alias |
| **D3** KG entity-indexing + co-occurrence | ~10,279–10,450 | `engine/kg_extract.py` (already exists) | 1 audit | ✅ clean | audit 2026-05-23: entity-index/co-occurrence is distinct from kg_extract's triple extraction; correctly stays in brain.py, no duplicate |

### B. Other oversized files — domains to split

| Domain | Source | → Target | Phase | Status | Note |
|---|---|---|---|---|---|
| admin: workflows | `handlers/admin.py` 217–1,136 | `handlers/admin_workflows.py` (flat, not pkg) | 1 | ✅ done | commit `8831427`; AdminWorkflowHandlers sub-mixin, MRO intact, server.py injection-list updated |
| admin: artifacts/files/sidecar/channels | admin.py | `handlers/admin_artifacts.py` (1600) | 3 | ✅ done | commit `b2ff754`; largest cluster (artifacts/files/channels/nodes/sidecar/services/backup/workers/refine/telegram/restart) |
| admin: costs/quotas UI | admin.py | `handlers/admin_costs.py` (193) | 3 | ✅ done | commit `b2ff754` |
| admin: skills | admin.py | `handlers/admin_agents.py` (merged) | 3 | ✅ done | commit `b2ff754`; merged into admin_agents (Rule 2 — tiny area) |
| admin: tool-settings/research/NER config | admin.py | `handlers/admin_config.py` (729) | 3 | ✅ done | commit `b2ff754`; + server_config/hooks |
| admin: teams | admin.py | `handlers/admin_agents.py` (merged) | 3 | ✅ done | commit `b2ff754`; merged into admin_agents |
| admin: agents | admin.py | `handlers/admin_agents.py` (723) | 3 | ✅ done | commit `b2ff754`; agents+teams+skills+files/commands |
| admin: KG/traces/audit observability | admin.py | `handlers/admin_observability.py` (1338) | 3 | ✅ done | commit `b2ff754`; +MCP/MemPalace/context-manager |

> **Convention note (set 2026-05-23 at the workflows split):** admin sub-handlers go to FLAT `handlers/admin_<area>.py` modules, each a mixin inherited by `AdminHandlerMixin`, each registered in `server._inject_server_globals()`'s `_handler_mod_names`. Avoids converting `admin.py`→`admin/__init__.py` (file-vs-package collision). The plan's `handlers/admin/<area>.py` package layout remains the ideal end-state but isn't worth the in-flight conversion risk.
| server: 7 background daemons (nested in `main()`) | `server.py` (nested in main()) | `server_daemons.py` (2002) | 3 | ✅ done | commit `746ed54`; lifted to module scope (symtable closure-completeness proof); srv-param threading; invariant #5 byte-identical. server.py −1932 |
| server: MemPalaceClient singleton | `server.py:69` | `server_lib/mempalace_client.py` | 3 | 🚫 deferred | NOT done — out of the daemon-lift risk budget; self-contained but low-value vs the core daemon move. Tracked as a follow-up, not blocking Phase 3 completion |
| server: bootstrap/init (optional) | `server.py` ~3,033–3,500 | `server_init.py` | 3 | 🚫 skipped (optional) | plan marked optional ("main() may stay"); not extracted — main() stays as the bootstrap home |
| chat: SSE streaming (format/keepalive/replay) | `handlers/chat.py` | `server_lib/sse_stream.py` (36) | 3 | ✅ done | commit `bb10f4a`; the formatter was inline dup, extracted format_sse/encode_sse; folds U3 (3 sites) |
| chat: GDPR-recovery modal state machine | `handlers/chat.py` ~51–200 | `handlers/gdpr_recovery.py` (52) | 3 | ✅ done | commit `bb10f4a`; module-level fns, plain move + re-export (no injection-list change) |
| db: node registry | `server_lib/db.py` 54–163 | `server_lib/node_registry.py` | 1 | ✅ done | commit `92c4a24`; module-level fns+state, zero DB dep; shared dict identity preserved |
| db: MemPalace sync cursor | `server_lib/db.py` ~1,324–1,422 | `server_lib/mempalace_sync.py` | 1 | ✅ done | commit `92c4a24`; ChatDB keeps delegating staticmethods; cycle avoided (local _db_safe + call-time _db_conn) |

### C. Cross-cutting reusable utilities (de-duplication)

| Utility | Copies today | → Target | Phase | Status | Note |
|---|---|---|---|---|---|
| U1 path-traversal guard | 5 divergent (classification, projects ×2, favourites, admin) | `server_lib/pathsafe.py` | 2 | ✅ done (partial) | commit `6a0a525`; 2 identical-skeleton copies merged, 3 left (merging would CHANGE security verdict); denylist-family copies 3→1 |
| U2 HTTP body read | 16 sites | (already centralized) | 2 | ✅ done (already-satisfied) | commit `c087db1`; canonical `_read_json` already exists — did NOT create competing module; repointed 3 inline-JSON stragglers; raw-JSON 4→1 |
| U3 SSE formatter | 3 sites | folded into `server_lib/sse_stream.py` | 3 | ✅ done | commit `bb10f4a`; 3 inline json.dumps SSE frames in chat.py → `encode_sse`. translate.py's divergent shape left (different wire behavior) |
| U4 repo-root path constant | ~82 sites (an idiom, not one value) | — | 2 | 🚫 SKIP (not-applicable) | investigated 2026-05-23: 82 occurrences resolve to DIFFERENT dirs by file depth, not one duplicated value; naive unify would rewrite ~half to the wrong dir. True repo-root sites already named locally (AGENTS_DIR/CONFIG_PATH/_REPO_ROOT). Cosmetic churn w/ real divergence risk → SKIP per principle #2 |
| U5 PII web/server rule sync | engine/pii_ner.py ↔ web/js/utils.js | `tools/check_pii_js_parity.py` (drift-CHECKER, gate-4b) | 3 | ✅ done | commit `793ca1e`; checker (not generator) diffs rule_id/category/action maps; caught a REAL pre-existing drift (`date` rule). Full codegen deferred — regex bodies differ by dialect; the metadata check catches the actual drift failure mode at near-zero risk |

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

---

## Tier E plan — registry data + ingest pipeline + remaining tool implementations (added 2026-05-23, user request)

**Why this exists:** after Tier C, a source-vs-report audit (2026-05-23) confirmed the refactor was faithful (0 surviving dup defs, all 24 modules real) but quantified what *remains* in brain.py's 16,950 lines. The user asked to (a) build the "Tier E" addendum I flagged (data-registry + ingest) AND (b) refactor the remaining `tool_*` implementations. This section is the scope.

**Measured remaining mass in brain.py (2026-05-23 audit):**
- `TOOL_DEFINITIONS` data literal (lines 560–1772): **~1,212 LOC** — pure data, the single biggest block.
- 48 `tool_*` implementations: **~2,420 LOC** (biggest cluster: files/shell/python/docs = 935 LOC / 10 tools).
- Document/ingest pipeline classes (`DocumentParser`/`DocumentChunker`/`IngestManager`/`IngestWatcher`, 7884–8650): **~700 LOC**.

**Sub-steps (each = one extraction = one commit, full gate between):**

| Step | Scope | → Target | LOC | Risk / gate |
|---|---|---|---|---|
| **E1** | file/shell/python/doc tool bodies (`tool_read_file`/`write_file`/`edit_file`/`list_directory`/`search_files`/`execute_command`/`python_exec`/`read_document`/`write_document`/`edit_document`) | `engine/tools/file_tools.py` | ~935 / 10 tools | **⚠️ chars-test FIRST** + eval. Heavy brain coupling: `_after_file_write` (35×), `_gdpr_anon_tool_text` (15×), `_get_artifact_session_folder`/`_record_session_read_path` — all reachable via lazy `_brain.`, same pattern as git/gmail tools. `python_exec`/`execute_command` are the highest-value (subprocess isolation). The 4-edit-site rule: TOOL_DEFINITIONS/GROUPS/DISPATCH entries STAY, fn moves. |
| **E2** | `TOOL_DEFINITIONS` data literal (the Anthropic tool-schema list) + `TOOL_DEFINITIONS_OPENAI`/`_TOOL_DEF_INDEX` builders | `engine/tool_schemas.py` | ~1,240 | LOW (pure data + 2 index dicts). brain re-imports the list + rebuilds indices. **TOOL_GROUPS (29 LOC) + TOOL_DISPATCH (70 LOC) STAY** — DISPATCH wires to the live `tool_*` callables (must resolve post-E1/E4); GROUPS is small membership data tightly read by the resolver. Verify: `resolve_active_tools` + `_render_tool_descriptions` + warmup all still see the same schema list (warmup byte-identity check MUST stay ✓ — this is the KV-prefix source). |
| **E3** | document/ingest pipeline (`DocumentParser`, `DocumentChunker`, `IngestManager`, `IngestWatcher`) | `engine/ingest.py` | ~700 | MED. Cohesive cluster, NOT in the original map (genuine new scope, surfaced by the audit). Touches `doc_convert` (already in engine/), MemPalace add-drawer, project config. Verify no cycle (ingest→brain lazy). |
| **E4** | remaining tool bodies grouped by domain: web/image (`exa_search`/`web_fetch`/`image_gen` already partly in engine), translation (`translate_text`/`translate_document`/`detect_language`/`get_glossary`/`list_glossaries`/`transcribe_audio`), delegation/worker (`delegate_task`/`task_status`/`worker_*`), context (`context_search`/`detail`/`recall`), skills/nodes (`use_skill`/`list_nodes`), MCP (`mcp_connect`/`disconnect`/`mcp_servers`), ask_* (`ask_user`/`ask_user_for_file`/`ask_llm`/`worker_ask_user`) | `engine/tools/{web,translate,delegation,context_tools,skill_node,mcp_tools}.py` | ~1,000 / ~28 tools | LOW–MED, per-group. Many are thin wrappers. `ask_user`/`ask_user_for_file` couple to the `_pending_answers` blocking machinery (verify the AskUserQuestion path). memory/mempalace tools (406 LOC / 7) — `tool_mempalace_query` already moved in C3; the rest (`mempalace_get_drawer`/`list_drawers`/`save_chat_to_memory`/`memory_*`) fold into `engine/mempalace_glue.py`. |

**Hard prerequisites & invariants (carry over from Tiers A–D):**
- **E1 needs a characterization test FIRST** (the file/python/exec tools have subprocess + artifact-tracking + GDPR-anon behavior with NO existing tests). E2/E3/E4 are data/wrapper moves — no chars-test required, but each runs the full gate + (where a tool feeds retrieval/answers) an eval check.
- **Warmup KV-prefix byte-identity** (`tools/check_warmup_prefix_stable.py --check`) MUST stay ✓ through E2 especially — TOOL_DEFINITIONS IS the tool-schema half of the warm prefix. Re-baseline + check around the move.
- **4-edit-site rule:** TOOL_DISPATCH stays in brain and must resolve every moved `tool_*` via re-export (the v8.27.0 image_gen bug class). Gate-3 verifies dispatch identity for every moved tool.
- One-way DAG (no top-level `import brain`), single-instance identity for any moved mutable global, eval Δ<0.10 per step that touches an answer path.

**Expected end state:** brain.py drops from ~16,950 to roughly **~13,000–13,500 LOC** (−E1 935 −E2 1,240 −E3 700 −E4 ~1,000 ≈ −3,875). What remains is genuinely core: TOOL_GROUPS/DISPATCH wiring, the runtime classes (ContextManager/MemoryStore/AgentConfig/ProjectManager/TaskRunner/MCPManager/WorkflowEngine/LocalProviderQueue), warmup/first-turn-prefix, the tool-resolver, GDPR/PII + classification config glue, KG entity-indexing, autodream, hooks — all thread-local/runtime-entangled by design.

**Execution:** same protocol as Tiers A–D — subagent-per-extraction, gate-2 (old def GONE), commit per green step, update + publish this report each time, flip the E-rows below. Resume by reading this report first.

### Master domain map — Tier E rows (planned)

| Domain | Source (brain.py) | → Target | Step | Status | Note |
|---|---|---|---|---|---|
| file/shell/python/doc tool bodies (10 tools) | scattered ~3,400–4,700 | `engine/tools/file_tools.py` | E1 | ✅ done | commit `c3fbc70` (chars-test `5ad37da` 13 cases first); −1,098 LOC; 10/10 dispatch-identity; eval brain 0.77→0.70 (Δ−0.07, within gate); warmup byte-identical |
| `TOOL_DEFINITIONS` schema data (+OPENAI/index) | 560–1,772 | `engine/tool_schemas.py` | E2 | ✅ done | commit `8d45315`; −1,220 LOC; warmup byte-identical (tool_names sha b80c9c46, 59 tools, identity True); DISPATCH/GROUPS stayed |
| document/ingest pipeline (4 classes) | 7,884–8,650 | `engine/ingest.py` | E3 | ✅ done | commit `fe8a0e6`; −697 LOC; 4 classes single-identity; `_ingest_watcher` singleton stayed (server.py assigns it); warmup byte-identical |
| remaining tool bodies (web/translate/delegation/context/skills/nodes/MCP/ask_*/worker, ~28 tools) | scattered | `engine/tools/{web,translate,delegation,context_tools,skill_node,mcp_tools}.py` + fold memory tools into `mempalace_glue.py` | E4 | ⬜ planned | per-group; ask_* couples to `_pending_answers` blocking path |

---

### Running totals
- Extractions completed: **23** (…, C1, C2, C3, **E1, E2, E3**) — Phases 1–4 done; **Tier E in progress (E1 ✅ E2 ✅ E3 ✅; E4 next)**
- Reverts: **0**
- `brain.py` line count: **25,182** (baseline) → _current: **13,935** (−11,247, −44.7%)
- `handlers/admin.py` line count: **5,416** → _current: **79** (−5,337, −98.5%; thin mixin core across 6 flat admin_*.py modules)
- `server.py` line count: **5,827** → _current: **3,895** (−1,932, −33.2%)
- `server_lib/db.py` line count: **1,985** → _current: 1,778 (−207)
- `handlers/chat.py` line count: 3,537 → 3,513 (−24; value was U3 de-dup)
- Net new production modules created: **27** — `engine/` (14): workflow, code_graph, context, scheduler, quotas, tools/git_tools, tools/gmail_tools, model_select, prompt_build (C1), tool_exec (C2), mempalace_glue (C3), **tool_schemas (E2), ingest (E3), tools/file_tools (E1)** · `server_lib/` (5): trace_audit, node_registry, mempalace_sync, pathsafe, sse_stream · `handlers/` (7): admin_workflows, admin_agents, admin_costs, admin_config, admin_observability, admin_artifacts, gdpr_recovery · top-level (1): server_daemons. *(Plus merges into existing modules: D2→classification.py, B3→pii_ner.py; U2 used existing reader; U4/MemPalaceClient/server_init skipped/deferred.)*
- Characterization tests added: **4** (`tests/test_scheduler_characterization.py` 18 — B2; `tests/test_tool_exec_characterization.py` 27 — C2; `tests/test_mempalace_wing_isolation.py` 9 — C3 security gate; `tests/test_file_tools_characterization.py` 13 — E1). Plus the C1 warmup byte-identity gate `tools/check_warmup_prefix_stable.py`.
- Drift-checkers added: **1** (`tools/check_pii_js_parity.py` — gate-4b; caught a real pre-existing PII map drift)
- Live duplicate definitions (the drift trap, principle #3): **0** — every extraction's Gate-2 confirmed the original `def`/`class` GONE from the source file.
- Reverts: **0**. Skips/already-satisfied (principled, documented): U4 (not-applicable), U2 (already centralized).
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

### 24 E1 file/shell/python/doc tools → engine/tools/file_tools.py — DONE  *(⚠️ highest-coupling Tier E; chars-test prereq `5ad37da`)*
- **Commit:** `c3fbc70`  ·  **Date:** 2026-05-23  ·  **Phase:** 5 (Tier E — E1)
- **Symbol(s):** `tool_read_file`/`write_file`/`edit_file`/`list_directory`/`search_files`/`execute_command`/`python_exec`/`read_document`/`write_document`/`edit_document` (10) + 8 cluster-private helpers (`_streaming_execute_command`, `_register_new_artifacts`, `_stray_write_warning`, `_strip_ansi`, `_build_shell_command`, `_write_md_table_to_sheet`, `_ABS_PATH_RE`, `_append_to_tool_result`)
- **Moved FROM:** brain.py (scattered ~3,400–4,700, the file/shell/python/doc tool cluster)
- **Moved TO:** engine/tools/file_tools.py (1,184, NEW)
- **Old code deleted?** YES — Gate-2: no `def` of the 10 tools in brain.py; re-export + dispatch refs only.
- **Callers re-pointed:** 0 — brain re-imports all 10 before TOOL_DISPATCH; the 10 dispatch entries + `server_lib/translate/document.py`'s `brain.tool_write_document` resolve via re-export.
- **Tests:** Gate 4 imports 18/18 · Gate 4b PII parity OK · Gate 5 150 tests, 3 known-NER fail (incl. the new 13-case file-tools chars-test, all green) · verdict PASS
- **Characterization test added?** YES (prereq, committed first as `5ad37da`) — `tests/test_file_tools_characterization.py`, 13 cases pinning read/edit/write_file, list/search, execute_command (TERM=dumb), python_exec (script_N pattern), read_document. Values PROBED from live tools (caught the bare-string-`current_agent` crash contract). **13/13 pass post-extraction.**
- **4-EDIT-SITE rule:** verified 10/10 dispatch-identity (`brain.TOOL_DISPATCH[k] is engine.tools.file_tools.tool_*`) + 10/10 re-export identity. TOOL_DEFINITIONS (now tool_schemas) + GROUPS + DISPATCH dict entries STAYED; only fn bodies moved.
- **EVAL GATE:** brain mean 0.77 → **0.70 (Δ −0.07)** — within Δ<0.10. Two big drops (F1 −, C1_ki −) both produced substantive answers (3,812 / 831 chars, no empty/error) — the documented F-bucket + citation judge-variance, not a tool break.
- **Warmup prefix:** byte-identical (these tools aren't in the system prompt).
- **brain.py delta:** 15,033 → 13,935 (−1,098)
- **Notes:** Shared runtime STAYS in brain, reached via lazy `import brain as _brain`: `_after_file_write`, `_gdpr_anon_tool_text`, `_route_to_node`, `get_tool_config`, `AGENTS_DIR`, `_current_agent`, `DocumentParser`. From engine direct: `_ok`/`_err`/`_get_artifact_session_folder`/`_record_session_read_path` (tool_exec), `_thread_local` (context), `_do_extract` (doc_convert lazy). `grep import brain` in file_tools.py = 0 (one-way DAG). `_maybe_qmd_reindex` (adjacent to read_file but a .md-reindex hook, not cluster code) correctly stayed.

### 23 E3 document/ingest pipeline → engine/ingest.py — DONE
- **Commit:** `fe8a0e6`  ·  **Date:** 2026-05-23  ·  **Phase:** 5 (Tier E — E3)
- **Symbol(s):** `DocumentParser`, `DocumentChunker`, `IngestManager`, `IngestWatcher`
- **Moved FROM:** brain.py:7,884–8,650 (the document parse → chunk → ingest → watch cluster)
- **Moved TO:** engine/ingest.py (758, NEW)
- **Old code deleted?** YES — Gate-2: no `class` def of any of the 4 in brain.py; re-export only.
- **Callers re-pointed:** 0 — `brain.DocumentParser.parse_image/parse_svg` (brain:2526/2533), handlers (projects ×10, admin_artifacts ×4, admin_workflows ×4 via `engine.` alias), server.py (`DocumentChunker.chunk`, `IngestWatcher()` instantiation) all resolve via re-export.
- **Tests:** Gate 4 imports 18/18 · Gate 4b PII parity OK · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a — cohesive class relocation; warmup byte-identity is the regression guard. (No existing tests on these classes; behavior unchanged via inheritance/identity.)
- **brain.py delta:** 15,730 → 15,033 (−697)
- **Notes:** Audit-surfaced new scope (NOT in the original Master map — flagged in the source-vs-report audit). `engine.doc_convert._do_extract` imported directly (already in engine/); brain runtime (`AGENTS_DIR`, `_qmd_debounced_embed`, `_yaml_escape`, `_parse_frontmatter`) via lazy `import brain as _brain` (grep count of top-level `import brain` = 0, one-way DAG). **`_ingest_watcher` singleton correctly STAYED** in brain — `server.py:3320` does `engine._ingest_watcher = engine.IngestWatcher()`, setting the brain-module attr; a re-exported binding wouldn't receive that. All 4 classes identity-True across brain↔engine. Warmup byte-identical.

### 22 E2 TOOL_DEFINITIONS schema data → engine/tool_schemas.py — DONE
- **Commit:** `8d45315`  ·  **Date:** 2026-05-23  ·  **Phase:** 5 (Tier E — E2)
- **Symbol(s):** `TOOL_DEFINITIONS` (59-tool Anthropic-shape list), `TOOL_DEFINITIONS_OPENAI` (auto-derived mirror + its build loop), `_TOOL_DEF_INDEX`, `_TOOL_DEF_OPENAI_INDEX`
- **Moved FROM:** brain.py:560–~1,790 (the tool-schema data literal + index builders)
- **Moved TO:** engine/tool_schemas.py (1,248, NEW — pure data, zero brain runtime, no top-level `import brain`)
- **Old code deleted?** YES — Gate-2: no `TOOL_DEFINITIONS = [` assignment in brain.py; module-level re-import only.
- **Callers re-pointed:** 0 — brain.py re-imports all four at line 565 (before first use), so the 23 bare-name uses (resolver, `_render_tool_descriptions`, warmup, `get_tool_breakdown`) resolve.
- **Tests:** Gate 4 imports 18/18 · Gate 4b PII parity OK · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a — pure data relocation; the warmup byte-identity check is the safety net.
- **E2-CRITICAL GATE — warmup byte-identity:** ✓ BYTE-IDENTICAL. `tool_names_sha256 b80c9c46` unchanged, 24-tool prefix intact — TOOL_DEFINITIONS is the schema half of the warm KV prefix, so this proves the model's tool input didn't move. No eval needed (input provably byte-identical).
- **brain.py delta:** 16,950 → 15,730 (−1,220)
- **Notes:** Identity verified `brain.TOOL_DEFINITIONS is engine.tool_schemas.TOOL_DEFINITIONS` (no schema-list copy to drift); 59 tools before==after. `TOOL_GROUPS` (membership data) + `TOOL_DISPATCH` (live-callable wiring) correctly STAYED. The single biggest block in brain.py, removed at near-zero risk.

### 21 C3 MemPalace query glue → engine/mempalace_glue.py — DONE  *(⚠️ wing-isolation security gate; test prereq `3b2115d`)*
- **Commit:** `100bba2`  ·  **Date:** 2026-05-23  ·  **Phase:** 4 (Tier C — C3, final)
- **Symbol(s):** `tool_mempalace_query`, `tool_save_chat_to_memory` + `_save_chat_to_memory_callback`, `_load_mempalace_config`/`_ensure_mempalace_importable` (+ config caches/import lock), `_get_reranker_model` (+ `_reranker_lock`/`_reranker_cache`), **new module-level `_wing_visible`** (lifted from the `tool_mempalace_query._visible` closure)
- **Moved FROM:** brain.py (~4864 config helpers, ~4902–5426 query tool, ~5798 save-chat, ~16753 reranker)
- **Moved TO:** engine/mempalace_glue.py (763, NEW)
- **Old code deleted?** YES — Gate-2: no def of `tool_mempalace_query`/`_load_mempalace_config`/`_get_reranker_model`/`_wing_visible` in brain.py; alias re-export only.
- **Callers re-pointed:** 0 — TOOL_DISPATCH ×2 (`mempalace_query`, `save_chat_to_memory`) resolve via re-export (import placed BEFORE the dispatch dict literal, mirroring image_gen); KG tools' `_load_mempalace_config`/`_ensure_mempalace_importable` + handlers' integration-status calls via re-export.
- **Tests:** Gate 4 imports 18/18 · Gate 4b PII parity OK · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** YES (prereq, committed first as `3b2115d`) — `tests/test_mempalace_wing_isolation.py`, 9 cases: 2 refuse-on-missing-project-id (no fall-back to user wing) + 7 cross-wing visibility. **C3 GATE: 9/9 PASS, 0 skipped** post-extraction (the 7 visibility cases un-skipped by promoting `_visible` → module-level `_wing_visible(wing, own_user, own_teams)`, re-exported on brain; behavior byte-identical to the closure).
- **EVAL GATE — confirmed via re-run:** first post-extraction run brain mean **0.65** (Δ−0.12, over the 0.67 floor) → did NOT instant-revert (protocol: borderline near floor ⇒ confirm re-run, not push-through). **Investigated:** the drop was concentrated in R2/F2/C2 (high-variance retrieval/refusal/citation axes); baseline R2 also had `tool_events=0` (the harness doesn't capture them — not a signal); R1 (+0.27) & C3_isms (+0.42) went UP, proving retrieval is NOT globally broken. **In-process proof:** queried the C3-extracted `tool_mempalace_query` directly with the R2 topic → correctly force-scoped to `project__f201b24ff6a2`, returned 5 drawers (retrieval + isolation intact). **Confirm re-run: brain mean 0.79** (Δ +0.02 vs baseline) — the 0.65 was a low-variance outlier (within the documented ±0.09 mean / ±0.38 max Mistral run-to-run noise). Two-run average 0.72, within gate.
- **Warmup prefix:** byte-identical (glue feeds nothing into the system prompt).
- **brain.py delta:** 17,556 → 16,950 (−606)
- **Notes:** mempalace pip imports stay LAZY inside functions; brain runtime (`ProjectManager`, `mempalace_activity`) via lazy `import brain as _brain`; no top-level `import brain` (cycle: brain imports glue for dispatch). Identity verified single-instance: `_reranker_cache`, `_load_mempalace_config`, config caches; `_save_chat_to_memory_callback` rebind path verified (server.py sets `brain._...`, glue reads `_brain._...`). STAYED: `_resolve_session_wing`/`_project_id_for_name`/`_memorize_mempalace_turns` (server_lib/db.py, ChatDB-entangled — the domain-map's "wing resolution" meant the query-tool scoping, not the session-wing resolver). **Config-path fix:** `_load_mempalace_config` resolved config.json via `dirname(__file__)`; from engine/ that needs double-dirname — adjusted, same resolved path verified.

### 20 C2 tool-execution layer → engine/tool_exec.py — DONE  *(⚠️ core path; chars-test prereq `3f87889`)*
- **Commit:** `9c9bc57`  ·  **Date:** 2026-05-23  ·  **Phase:** 4 (Tier C — C2)
- **Symbol(s):** `_ok`/`_err`, `_get_artifact_session_folder`, dedup cluster (`_tool_dedup_lock`/`_tool_dedup`/`_TOOL_DEDUP_TTL`/`_dedup_sid`/`_dedup_state`/`_dedup_gc_locked`/`_check_tool_dedup`/`reset_tool_dedup`), read-path tracker (`_session_read_paths_lock`/`_session_read_paths`/`_session_read_paths_sid`/`_SESSION_READ_PATHS_MAX`/`_record_session_read_path`/`_read_doc_cache_session_paths`), result-processing (`_BASE64_DATA_RE`/`_BASE64_RAW_RE`/`_sanitize_tool_result`/`_compress_old_tool_results`/`TOOL_RESULT_BUDGET_THRESHOLD`/`TOOL_RESULT_PREVIEW_SIZE`/`_apply_tool_result_budget`/`_MICROCOMPACT_TOOLS`/`_COMPACT_TOOL_ARGS`/`_MICROCOMPACT_EXEMPT`/`_microcompact`/`_find_tool_name_for_result`/`_find_tool_name_for_block`)
- **Moved FROM:** brain.py (scattered: ~2841, ~3064, ~16646–16850, ~17836–18070)
- **Moved TO:** engine/tool_exec.py (475, NEW)
- **Old code deleted?** YES — Gate-2 verified 5 representative symbols (`_check_tool_dedup`/`_microcompact`/`_sanitize_tool_result`/`_record_session_read_path`/`_get_artifact_session_folder`): "no definition in brain.py".
- **Callers re-pointed:** 0 — brain.py does module-level `from engine.tool_exec import _ok, _err, ...` so bare in-brain `_ok(...)`/`_err(...)` calls bind to the module global; handlers' `engine._microcompact`/etc. resolve via the brain-alias re-export.
- **Tests:** Gate 4 imports 18/18 · Gate 4b PII parity OK · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** YES (prereq, committed first as `3f87889`) — `tests/test_tool_exec_characterization.py`, 27 cases pinning dedup thresholds/exemptions/50-bound, sanitize/_mcp_images, compress 500→200 boundary, microcompact >100 gate + tokens_freed//4, read-path abs/dedup/scope/256-cap, `_ok`/`_err` shape, artifact-folder naming. **45/45 (tool_exec + scheduler) pass post-extraction.**
- **EVAL GATE:** brain mean 0.77 → **0.75 (Δ −0.02)** — well within Δ<0.10. Tool-exec is a pure relocation (no prompt/answer-shape change), the chars-tests prove byte-identical behavior, and the eval confirms no quality movement.
- **Warmup prefix:** byte-identical (tool-exec feeds nothing into the system prompt). An apparent "drift" mid-dev was the hour-rounded timestamp rolling over — confirmed against the clean tree, not C2.
- **brain.py delta:** 17,862 → 17,556 (−306)
- **Notes:** Identity verified single-instance for every moved mutable global (`_tool_dedup`/`_session_read_paths` + their locks, `_ok`, `_brain.TaskCancelled is brain.TaskCancelled`) — no concurrency/dedup split. `extract_attachment_text` + `_gdpr_anon_tool_text` correctly STAYED (PII/pseudonymizer-coupled, not pure). `_get_reranker_model` (sits between read-path globals and helpers in brain.py) left untouched — not a C2 target.

### 19 C1 model-select + system-prompt build → engine/{model_select,prompt_build}.py — DONE  *(⚠️ KV-cache sensitive)*
- **Commit:** `f83e72e`  ·  **Date:** 2026-05-23  ·  **Phase:** 4 (Tier C — C1)
- **Symbol(s):** *prompt_build:* `_build_system_prompt`, `_apply_system_prompt_postprocess`, `_system_prompt_cache`/`_SYSTEM_PROMPT_CACHE_TTL`, `_GDPR_ANON_CLAMP`, `_project_preamble_text`/`_workflow_run_preamble_text`/`_artifact_folder_preamble_text`/`_files_in_chat_preamble_text`. *model_select:* `MODEL_PROFILES`, `get_model_profile`/`resolve_model_settings`/`resolve_profile_token_config`/`resolve_profile_limits`, `resolve_provider_for_model`/`clear_provider_cache` + `_provider_cache`/`_provider_cache_lock`/`_provider_cache_time`, `ProviderKeyPool`/`_get_key_pool`/`_normalize_api_keys`/`invalidate_key_pool`/`mark_api_key_exhausted`/`_KEY_EXHAUST_TTL`/`_key_pools`/`_key_pools_lock`
- **Moved FROM:** brain.py (scattered: ~1877 profiles, ~11089 provider/key-pool, ~18110 prompt cluster)
- **Moved TO:** engine/prompt_build.py (767, NEW) + engine/model_select.py (366, NEW)
- **Old code deleted?** YES — Gate-2 verified all 3 representative symbols (`_build_system_prompt`, `resolve_provider_for_model`, `MODEL_PROFILES`): "no definition in brain.py", alias re-export only.
- **Callers re-pointed:** 0 — `build_first_turn_prefix` (STAYS in brain, the warmup recipe) calls the moved `_build_system_prompt` via re-export; chat/scheduler/warmup/background reach `resolve_provider_for_model` via the brain/`engine`-alias attr.
- **Tests:** Gate 4 imports 18/18 · Gate 4b PII parity OK · Gate 5 80 pass / 3 known-NER fail · **chars-tests 45/45 (tool_exec + scheduler)** · verdict PASS
- **Characterization test added?** n/a for C1 (the ⚠️ chars-test prereq was for C2). C1's safety net is the warmup byte-identity check + eval.
- **C1-SPECIFIC GATE — warmup KV-prefix BYTE-IDENTICAL:** `tools/check_warmup_prefix_stable.py` (NEW). Captured pre-extraction baseline (system_prompt sha `b89c5a14`, len 3357; tool-set sha `b80c9c46`, 24 tools), `--check` after extraction = ✓ byte-identical. **This is the decisive evidence** the model's prefix is unchanged → KV cache still hits.
- **EVAL GATE:** brain mean 0.77 → **0.71 (Δ −0.06)** — within Δ<0.10 gate AND within the ±0.09 Mistral run-to-run variance. Per-question swings are large in BOTH directions (R1 +0.20, P2 +0.18, M1 +0.15 up; F1 −0.68, C1_ki −0.65 down) but offset to a small net; F1/C1_ki answers inspected = full substantive/refusal answers (not empty/error), the classic F-bucket + citation judge-variance signature. Byte-identical prefix + non-empty answers ⇒ variance, not regression.
- **brain.py delta:** 18,814 → 17,862 (−952)
- **Notes:** Identity verified single-instance across brain↔engine for every moved mutable global (`_provider_cache`/`_key_pools`/`MODEL_PROFILES`/`_system_prompt_cache`/`_GDPR_ANON_CLAMP`) — no concurrency split. New modules have **no top-level `import brain`** (lazy `import brain as _brain` inside fns; `from engine.context import _thread_local` at top is cycle-free). **Deviation from boundary map:** `LocalProviderQueue`/`_ProviderTicket` at ~11188 is the provider *concurrency queue* (distinct subsystem), NOT key-pools — correctly LEFT in brain. The 3 unused `*_preamble_text` helpers (dead in sidecar arch) moved anyway as a cohesive cluster; `_artifact_folder_preamble_text` (live, called from handlers/chat.py) resolves via re-export. `get_api_model_id`/`get_inference_params`/`_apply_inference_to_payload` (sidecar-shared) correctly STAYED.

### 18 server.py daemons — lift 7 loops out of main() → server_daemons.py — DONE  *(⚠️ riskiest move)*
- **Commit:** `746ed54`  ·  **Date:** 2026-05-23  ·  **Phase:** 3 (server_daemons)
- **Symbol(s):** `_file_change_watcher`, `_mempalace_miner_loop`, `_mempalace_chat_sync_loop`, `_project_sync_loop`, `_user_profile_loop`/`_user_profile_cycle`, `_warmup_keeper_loop` + their helper/constant siblings (`_ensure_mempalace_yaml`, `_extract_references_from_tool_payload`, `_file_mtimes`, `_MEMPALACE_YAML_MARKER`, …)
- **Moved FROM:** server.py — all NESTED as closures inside `main()`
- **Moved TO:** server_daemons.py (2002 lines, NEW)
- **Old code deleted?** YES — Gate-2: no daemon def remains in server.py; only the 6 retargeted thread-spawn start-sites reference them.
- **Callers re-pointed:** start-sites STAY in main() (startup sequencing unchanged), retargeted to `server_daemons.X` with `args=(_srv,)`.
- **Tests:** Gate 4 imports 18/18 (incl server) · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a — daemons only run under a live server; covered instead by the **symtable closure-completeness proof** (see Notes) + the user's manual chat sanity-check post-run.
- **server.py delta:** 5,827 → 3,895 (−1,932)
- **Notes:** The ⚠️ risk was nested closures losing `main()`-locals when lifted. Discharged by a `symtable` free-variable analysis (the gate's runtime blind-spot): proved EVERY global-unassigned name in every lifted function resolves to a module global / builtin / the `srv` param / a function-local — "NONE unresolved." Closure surface was shallow (not the feared deep entanglement). Server-internal singletons reached via a single `srv` param (`= sys.modules['server']`, passed at spawn); peer names (engine/ChatDB/_db_conn/_resolve_session_wing/_auth_mod) imported directly; `_file_mtimes`/`_MEMPALACE_YAML_MARKER` became module-level. No cycle (server_daemons imports brain+server_lib, never server). **Invariant #5 byte-identical:** the chat-sync classifier's `engine._thread_local.current_user_id` set/restore (around `classify_chat_for_memory`) unchanged, same try/finally. **Left in main()** (not targeted): `_backfill_chat_index`/`_cleanup_orphaned_chat_indexes`/`_qmd_index_keeper` — index-keepers; `_backfill` has a PRE-EXISTING latent `ChatSession` NameError, so moving it would change the failure mode (left untouched, surgical). **MemPalaceClient deferred** (out of risk budget); **server_init skipped** (plan-optional).

### 17 chat.py splits — SSE formatter (+U3) + GDPR-recovery — DONE
- **Commit:** `bb10f4a`  ·  **Date:** 2026-05-23  ·  **Phase:** 3 (chat split + U3)
- **Symbol(s):** *sse_stream:* `format_sse`, `encode_sse`, `KEEPALIVE`. *gdpr_recovery:* `_gdpr_recovery_pending`/`_lock`/`_register`/`_clear`, `deliver_gdpr_recovery_choice`
- **Moved FROM:** handlers/chat.py (inline SSE formatting ×3 + GDPR-recovery state machine ~51–200)
- **Moved TO:** server_lib/sse_stream.py (36, NEW, stdlib-only) + handlers/gdpr_recovery.py (52, NEW)
- **Old code deleted?** YES — Gate-2 clean for both; SSE inline dupes collapsed to `encode_sse` calls, GDPR-recovery defs gone (re-exported from chat.py).
- **Callers re-pointed:** SSE 3 inline sites → `encode_sse`; GDPR-recovery via chat.py re-export (shared dict/lock identity preserved so the test's `.clear()` hits the live registry).
- **Tests:** Gate 4 imports 18/18 · **tests/test_chat_worker_helpers 15/15 pass** · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a (existing test_chat_worker_helpers pins the path)
- **chat.py delta:** 3,537 → 3,513 (−24; the SSE win is de-duplication not raw lines — inline formatting was small but repeated)
- **Notes:** **U3 folded here** (3 inline `json.dumps` SSE frames → `encode_sse`). translate.py's divergent `ensure_ascii=False` SSE shape left alone (folding would change wire behavior — same discipline as U1/U2). chat-specific streaming machinery (`_stream_live_to_client`, `build_chat_event_callback`, worker, LiveStream attach/replay) STAYED; `LiveStream` lives in server.py — untouched (Resumable Streaming invariants preserved). GDPR-recovery = module-level fns → plain move (NOT a sub-mixin), so no `_inject_server_globals` change needed.

### 16 admin.py full decomposition (5 flat sub-modules) — DONE
- **Commit:** `b2ff754`  ·  **Date:** 2026-05-23  ·  **Phase:** 3 (admin split)
- **Symbol(s):** the remaining `_handle_*` clusters → 5 mixins: `AdminAgentsHandlers`, `AdminCostsHandlers`, `AdminConfigHandlers`, `AdminObservabilityHandlers`, `AdminArtifactsHandlers`
- **Moved FROM:** handlers/admin.py (4,503 lines of route handlers)
- **Moved TO:** handlers/admin_agents.py (723), admin_costs.py (193), admin_config.py (729), admin_observability.py (1338), admin_artifacts.py (1600) — all NEW flat modules
- **Old code deleted?** YES — Gate-2 spot-checked 6 methods (`_handle_create_agent`/`_quota_me`/`_tool_settings_save`/`_kg_stats_global`/`_artifacts_browse`/`_refine`): 0 occurrences in admin.py. Only `_serve_static` def remains.
- **Callers re-pointed:** 0 — `AdminHandlerMixin` inherits all 6 sub-mixins (incl. workflows), so `BrainAgentHandler` MRO unchanged; all 5 representative methods verified resolving on the composed handler FROM their new module.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail (stable 2x) · verdict PASS
- **Characterization test added?** n/a (HTTP handlers; behavior unchanged via inheritance)
- **handlers/admin.py delta:** 4,503 → **79** (−4,424) — thin core: `AdminHandlerMixin` inheriting 6 sub-mixins + shared `_serve_static`
- **Notes:** Continues the proven workflows-split pattern. **Judgment (Rule 2):** realized the plan's 7-way intent as 5 cohesive modules — merged tiny areas (teams+agents+skills → admin_agents; tool-settings+research+NER+server-config+hooks → admin_config). **Invariant #2 (the silent-NameError trap):** all 5 new mixins imported (server.py:940–944) AND registered in `_inject_server_globals` `_handler_mod_names` (server.py:969–973) — injection-loop verified each receives the full 116-global set (parity with admin_workflows). Area-private helpers co-located with their sole-area callers (no cross-module private splits); only `_serve_static` (generic) left shared.

### 15 B4 quotas / cost / rate-limit — DONE
- **Commit:** `12127c1`  ·  **Date:** 2026-05-23  ·  **Phase:** 3 (B4)
- **Symbol(s):** `CostTracker`, `QuotaManager`, `RateLimiter`, `QuotaExceededError`, `_cost_tracker`/`_quota_manager`/`_rate_limiter` singletons, `COST_DB`/`_cost_db_pool`/`_cost_conn`, `_cost_rates`/`_get_cost_rate`/`_compute_cost`, `QUOTA_DEFAULTS`/`_quota_default_role_limits`
- **Moved FROM:** brain.py (scattered quota/cost/rate-limit code)
- **Moved TO:** engine/quotas.py (900 lines, NEW) — **single module, not the planned cost.py + quotas.py split**
- **Old code deleted?** YES — Gate-2: all 3 class defs gone from brain.py; brain re-exports + breadcrumb comments.
- **Callers re-pointed:** 0 — singletons instantiated server.py:3262–3264 (unchanged, via alias); handlers use `engine._cost_tracker`/`_quota_manager`; doc_convert `from brain import _cost_tracker` (call-time) — all resolve via re-export.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS · no tests import this subsystem (grep empty)
- **Characterization test added?** n/a (no existing tests; not flagged ⚠️ in plan — B2/C2 were the ⚠️ paths)
- **brain.py delta:** 19,630 → 18,814 (−816)
- **Notes:** **Module decision = single `engine/quotas.py`** (deviation from plan's 2-way split, Rule 2): QuotaManager reads CostTracker's `cost_log`, QuotaExceededError is the quota contract — cohesive; splitting would add a quotas→cost cross-module dep for zero independent reuse. costs.db pool moved with CostTracker (verified not shared — RateLimiter in-memory, QuotaManager reads via CostTracker). `_log_call_cost` correctly LEFT in brain (coupled to `_key_pools`/`_current_agent`/`_thread_local`/`_rate_limiter`), reaches the moved helpers via alias. `QuotaExceededError` caught nowhere in live code (send_message gate gone since Phase 5). `is_model_local`/`_models_config` stay in brain, reached lazily. CostTracker body byte-identical.

### 14 B3 PII regex scanner + U5 parity drift-checker — DONE
- **Commit:** `793ca1e`  ·  **Date:** 2026-05-23  ·  **Phase:** 3 (B3 + U5)
- **Symbol(s):** `_pii_rules`, `_pii_scan_text`, `_pii_scan_bare_identifiers`, `PII_RULE_CATEGORIES`, `PII_DEFAULT_CATEGORY_ACTIONS`
- **Moved FROM:** brain.py (GDPR/PII regex scanner cluster, ~770 LOC post line-shift)
- **Moved TO:** engine/pii_ner.py (merged with the existing spaCy-NER half — pii_ner.py top-level imports are stdlib-only, spaCy lazy-loaded in `load_models`, so the regex scanner stays import-light; no separate pii_scan.py needed)
- **Old code deleted?** YES — Gate-2: no `_pii_scan_text`/`_pii_rules` def in brain.py; brain re-exports all 5 (back-compat: `brain.X`/`engine.X`/`from brain import _pii_scan_text` all resolve).
- **Callers re-pointed:** 0 — re-export covers tests (`brain._pii_scan_text`), engine/classification.py (`from brain import`), handlers (`engine.X`). Config-coupled helpers (`_pii_effective_action`, `_get_gdpr_scanner_config`, `_pii_email_allowed`) stay in brain (not pure).
- **Tests:** Gate 4 imports 18/18 · Gate 4b parity OK · Gate 5 80 pass / 3 known-NER fail · **GDPR + pseudonymizer suites 41/41 pass** · verdict PASS
- **Characterization test added?** n/a — existing GDPR/pseudonymizer tests already pin behavior (41 tests); they're the characterization layer for this path.
- **brain.py delta:** 20,386 → 19,613 (−773)
- **Notes:** **Rule order is a correctness invariant** (first-match-wins + overlap suppression: context-gated before bare-digit, credit_card after national-IDs, phone after national-IDs) — body extracted as exact byte-range, NOT re-typed; verified by 41/41 tests + functional smoke. Scanner is pure (re + stdlib, zero brain dep; `_pii_scan_text` lazy-imports brain only for the config-action resolver — cycle-safe). **U5 = drift-CHECKER** (`tools/check_pii_js_parity.py`, gate-4b), not a generator: diffs Python rule_ids + category/action maps vs `web/js/utils.js` PIIScanner — regex *bodies* differ by dialect (re vs RegExp) so aren't diffed; only the metadata that silently drifts. **First run caught a REAL pre-existing drift** — Python `date` rule had no JS category entry (relied on `||'personal'` fallback); fixed in utils.js. CLAUDE.md note updated for the moved location.

### 13 B2 scheduler + task runner — DONE  *(prereq: chars-test `b09c5dd`)*
- **Commit:** `2ba75be`  ·  **Date:** 2026-05-23  ·  **Phase:** 3 (B2 — ⚠️ core path)
- **Symbol(s):** `class Scheduler` (full: CRUD + `get_due_tasks` atomic claim + `_execute_scheduled` + `_run_loop` poll), `_calc_next_run`/`_calc_next_from_last`, `_validate_thinking_level_for_model`, `tool_schedule_list`/`history`, DB pool `SCHEDULER_DB`/`_sched_db_pool`/`_sched_conn`
- **Moved FROM:** brain.py:12,950–15,641 (scheduler + task-runner cluster)
- **Moved TO:** engine/scheduler.py (1407 lines, NEW)
- **Old code deleted?** YES — Gate-2: no `class Scheduler`/`_execute_scheduled`/`_calc_next_run` def in brain.py; alias + instantiation + method-call refs only.
- **Callers re-pointed:** 0 repointed — `Scheduler()` instantiation (server.py:3254) + poll-thread start STAYED; class resolves via alias. The workflow-history subsystem + schedule-sharing helpers that piggyback on scheduler.db stay in brain, reach `_sched_conn`/`SCHEDULER_DB` via re-export.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · **18/18 scheduler-characterization tests pass post-extraction** · verdict PASS
- **Characterization test added?** YES — `tests/test_scheduler_characterization.py` (commit `b09c5dd`, the B2 prerequisite per plan §1.5): 18 tests pinning `_calc_next_run` interval math, thinking-level gate, tool_profile→purpose. Gate blind spots (DB CRUD, atomic-claim races, `_execute_scheduled` delegate) remain — covered by live schedule eval, not the gate.
- **brain.py delta:** 21,689 → 20,386 (−1,303)
- **Notes:** Hardest seam = `_thread_local` + invariant #5. Resolved `_thread_local` via clean `from engine.context import _thread_local` (context.py is below scheduler in the DAG — no cycle); 3-way instance identity verified True (brain≡context≡scheduler). **Invariant #5 byte-identical:** `_execute_scheduled` builds `ExecutionContext` + calls `init_thread_context(..., agent_config=target)` BEFORE the sidecar delegate, same vars/order. All other brain-runtime via a lazy `_LazyBrain` proxy (`import brain` on `__getattr__`); sidecar + ChatDB kept lazy in-method. 4-site tool reg + dispatch-identity True. `_VALID_PURPOSES`/`_VALID_TOOL_PROFILES` correctly LEFT in brain (tool-resolver region, not scheduler).

### 12 U4 repo-root constant — SKIP (not-applicable)
- **Commit:** — (no change)  ·  **Date:** 2026-05-23  ·  **Phase:** 2
- **Investigation:** 82× `os.path.dirname(os.path.abspath(__file__))` (+6 single-dirname). They do NOT share one value — they resolve to DIFFERENT directories by file depth (root files → repo root; depth-1 files use double-dirname for root, single-dirname for their own module dir). Verified: single `dirname` in handlers/admin.py → `…/handlers`, not root.
- **Decision:** SKIP. U4 was a misconception — a common idiom resolving per-depth, not a duplicated constant. A naive global unify would silently rewrite ~half the sites to the wrong dir. The genuine repo-root sites are already named locally (`AGENTS_DIR`/`CONFIG_PATH`/`_REPO_ROOT`). Consolidating would touch ~38 heterogeneous files + add new import edges into brain/server/handlers (cycle risk) for zero behavioral/token benefit. Per governing principle #2 (risky/half-done worse than none), SKIP is the principled call.
- **Old code deleted?** n/a (no change). **Tests:** n/a (nothing changed).

### 11 U2 HTTP body read — DONE (already-satisfied)
- **Commit:** `c087db1`  ·  **Date:** 2026-05-23  ·  **Phase:** 2
- **Symbol(s):** existing `_read_json` (server.py:1018) — the canonical reader, already used by 60+ sites (CLAUDE.md: do NOT re-extract).
- **Decision:** did NOT create `server_lib/http_util.py` (would be a competing copy = anti-dedup). Repointed 3 inline `json.loads(rfile.read(...))` stragglers in chat.py (`_handle_chat_answer`, `_handle_chat_gdpr_recovery`, `_handle_attachment_scan`) → `self._read_json()`.
- **Old code deleted?** YES — 3 inline raw-body reads collapsed to the helper call; raw-JSON straggler count 4→1.
- **Callers re-pointed:** 3 sites. **Left with reason:** `_handle_gdpr_scan_text` (load-bearing 200KB→413 cap `_read_json` lacks) + 6 multipart raw-BYTES sites (boundary parsing, per-site caps — JSON reader doesn't fit).
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **delta:** −3 LOC, no new module.
- **Notes:** Behavior preserved (both yield `{}` on empty body; try/except still maps malformed JSON → 400).

### 10 U1 path-traversal guard — DONE (partial, faithful)
- **Commit:** `6a0a525`  ·  **Date:** 2026-05-23  ·  **Phase:** 2
- **Symbol(s):** new `server_lib/pathsafe.validate_path` + `HARD_DENY` constant
- **Moved FROM:** inline guards in handlers/classification.py (`_validate_scan_path` + `_BLOCKED_PREFIXES`) + handlers/projects.py (`_project_input_folder_validate` + `_PROJECT_INPUT_FOLDER_FORBIDDEN`)
- **Moved TO:** server_lib/pathsafe.py (93 lines, NEW) — parameterized: shared realpath + `HARD_DENY` (`/etc /var /usr /bin /sbin /System /Library/Keychains`) + `os.sep` boundary; per-site policy via kwargs (`allowed_roots`, `deny_agents_dir`, `must_exist`/`must_be_dir`, `expand_user`).
- **Old code deleted?** YES — both inline denylists/skeletons gone from the 2 repointed files; `validate_path` defined once (gate-2 clean).
- **Callers re-pointed:** 2 of 5 (the identical-skeleton copies). **Left with reason (security):** admin._validate_file_path (loose `startswith` boundary, no `os.sep` — tightening = behavior change); image-confinement trio (projects:328 + favourites ×2 — single-root, NO denylist — adding one = behavior change). Divergent denylist-family copies 3→1.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a — but per-site policy preservation VERIFIED: throwaway harness confirmed identical old-vs-new allow/deny verdict + resolved path across representative allowed+denied paths for both repointed sites.
- **Notes:** Cosmetic-only side effect: 2 projects error *strings* reworded (decision unchanged). The KEY discipline here — refused to homogenize the 5 copies because they DIVERGE in security-meaningful ways (denylist presence, boundary strictness, allowlist vs allow-by-default); merging all would silently change security verdicts. Faithful partial > risky full.

### 9 B1 _thread_local execution context — DONE
- **Commit:** `5e56783`  ·  **Date:** 2026-05-23  ·  **Phase:** 2 (shared seam — prerequisite for B2–B4)
- **Symbol(s):** `_thread_local` (threading.local), `ExecutionContext`, `init_thread_context`, `clear_thread_context`
- **Moved FROM:** brain.py:10,878 (83-line context block)
- **Moved TO:** engine/context.py (105 lines, NEW; stdlib-only, no `import brain` → low-level cycle-free base)
- **Old code deleted?** YES — Gate-2 grep: `_thread_local = threading.local()` gone from brain.py; brain re-exports all four.
- **Callers re-pointed:** 0 — all 291 `_thread_local` refs (`brain.`, `engine.` [= the `import brain as engine` alias], bare) resolve to the SAME instance via re-export. **Instance identity verified True** (`brain._thread_local is engine.context._thread_local`).
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a (relocation, not behavior change)
- **brain.py delta:** 21,762 → 21,689 (−73)
- **Notes:** RELOCATION ONLY (not DI, per scope). #1 risk was instance-identity (a thread-local's whole point is shared identity — two instances = silent concurrent context bleed); verified True. Surfaced a load-bearing fact for the whole refactor: the `engine` name in handlers/server is `import brain as engine` (the brain module aliased), NOT the `engine/` package. Establishes the canonical shared-state home so Phase-3 B2–B4 can `from engine.context import _thread_local` instead of `import brain`.

### 8 admin: workflow handlers split — DONE
- **Commit:** `8831427`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (admin split)
- **Symbol(s):** 17 `_handle_workflow_*` route handlers (list/save/delete/run/list_executions/get_execution/approve/cancel/history/history_delete_run/history_delete_bulk/history_get/upload_file/promote_session/get_or_create_session/run_file_download/run_file_preview) + 5 workflow-only helpers (`_seed_artifacts_for_run`, `_lookup_workflow_run_session`, `_workflow_run_paths`, `_workflow_run_paths_classified`, `_workflow_run_can_access`)
- **Moved FROM:** handlers/admin.py:217–1136 (Workflow Handlers block)
- **Moved TO:** handlers/admin_workflows.py (949 lines, NEW) — new `AdminWorkflowHandlers` mixin
- **Old code deleted?** YES — Gate-2 grep: `_handle_workflow_save` etc. no longer defined in admin.py; now in admin_workflows.py.
- **Callers re-pointed:** 0 — `AdminHandlerMixin(AdminWorkflowHandlers)` inherits the methods, so `BrainAgentHandler`'s MRO is unchanged and route dispatch resolves them exactly as before (verified `_handle_workflow_run` accessible on the composed handler via MRO).
- **Tests:** Gate 4 imports 18/18 (incl. handlers.admin) · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a (HTTP handler methods; behavior unchanged via inheritance)
- **handlers/admin.py delta:** 5,416 → 4,503 (−913)
- **Notes:** **Invariant #2 trap handled** — `server._inject_server_globals()` injects server globals into each handler module's `__dict__` keyed off `Mixin.__module__`. The moved methods now live in a NEW module, so it had to be added to `_handler_mod_names` (server.py:963) or bare-name lookups (`engine`, `_db_conn`, `sqlite3`) would `NameError` at runtime — invisible to import-gate. Verified the new module gets identical global resolution to admin.py (parity). server.py:939 imports the mixin. Chose flat `handlers/admin_*.py` over the `handlers/admin/` package (collision-risk avoidance) — convention set for the remaining Phase-3 admin splits.

### 7 db.py splits — node-registry + mempalace-sync — DONE
- **Commit:** `92c4a24`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (db splits)
- **Symbol(s):** *node_registry:* `_node_registry`/`_node_commands`/`_node_lock`, `_load_node_config`/`_save_node_config`/`_init_node_registry`/`_node_submit_command`. *mempalace_sync:* `mempalace_sessions_needing_sync`/`load_new_messages`/`last_user_id_before`/`update_cursor`
- **Moved FROM:** server_lib/db.py:54–163 (node registry, module-level fns+state) + ~1,324–1,422 (mempalace cursor, ChatDB `@staticmethod`s)
- **Moved TO:** server_lib/node_registry.py (117 lines) + server_lib/mempalace_sync.py (135 lines), both NEW
- **Old code deleted?** YES — Gate-2 grep: `_node_registry` def + `mempalace_update_cursor` def gone from db.py; alias re-exports + thin delegating staticmethods only.
- **Callers re-pointed:** 0 — node-registry's 12 admin.py + 2 server.py sites resolve via re-export/globals-injection (shared dict identity preserved → in-place mutations still land); mempalace's 10 `ChatDB.mempalace_*()` sites preserved by delegating staticmethods.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a (peripheral, self-contained)
- **db.py delta:** 1,985 → 1,778 (−207). `ChatDB` core untouched, stays in db.py.
- **Notes:** node-registry touches only config.json + in-memory dicts (zero DB dep). mempalace-sync: a naive top-level `from server_lib.db import _db_conn,_db_safe` created a real import cycle (db.py class body re-enters the half-built module) — resolved by a local `_db_safe` copy + call-time `_db_conn` import; **verified clean in BOTH import orders**. The `chat_mempalace_sync` CREATE TABLE stays in ChatDB init (schema bootstrap belongs with the DB).

### 6 A5 trace manager + audit trail — DONE
- **Commit:** `fa146c3`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (Tier A — riskiest, widely-called `_audit_log`)
- **Symbol(s):** `TraceManager`, `AuditLog`, `_traces_conn`/`_audit_conn`, `_traces_db_pool`/`_audit_db_pool`, `TRACES_DB`/`AUDIT_DB`, `_AUDIT_ACTION_MAP`, `_audit_summarize_args`/`_audit_summarize_result`, singleton holders `_audit_log`/`_trace_manager`
- **Moved FROM:** brain.py:15043–15437 (trace + audit subsystem)
- **Moved TO:** server_lib/trace_audit.py (428 lines, NEW)
- **Old code deleted?** YES — Gate-2 grep: no surviving class/def/DB-const in brain.py; single `from server_lib.trace_audit import (...)` alias block.
- **Callers re-pointed:** 0 repointed — 58 `_audit_log` + 13 `_trace_manager` sites repo-wide (brain.py, handlers/admin, handlers/chat, engine/classification ×8 lazy, providers, server.py) ALL resolve via the brain/`engine`-alias module attr. No churn.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a (Tier A; but extra-verified the startup-rebind invariant — see Notes)
- **brain.py delta:** 22,144 → 21,762 (−382)
- **Notes:** Lowest-coupling resolution possible — `_audit_log`/`_trace_manager` are module-level **singletons** (`None` at load, instantiated by server.py:3269–3270 via `engine._audit_log = engine.AuditLog()`). The closure touches ONLY stdlib + `AGENTS_DIR` (recomputed locally from `__file__`, verified equal) → **zero brain-runtime dependency, no lazy import, no cycle** (server_lib sits below brain in the DAG). Critical invariant verified by simulation: server.py's startup rebind sets the brain module attr, so every bare-name/`engine.`/`_brain.` reader sees the live singleton. This was the highest-risk Tier-A move; clean.

### 5 A4 gmail tools — DONE
- **Commit:** `f8f3a1e`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (Tier A pure win)
- **Symbol(s):** `tool_gmail_inbox`/`read`/`search`/`send`/`reply` + helpers `_gmail_config`, `_decode_mime_header`, `_get_email_body`
- **Moved FROM:** brain.py:4770–5086 (Gmail Tools block)
- **Moved TO:** engine/tools/gmail_tools.py (336 lines, NEW)
- **Old code deleted?** YES — Gate-2 grep: no `def tool_gmail_*` in brain.py; alias re-export + dispatch refs only.
- **Callers re-pointed:** `_gmail_config` (integration-status helper) resolves via re-export; all 5 dispatch entries via alias.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a (Tier A)
- **brain.py delta:** 22,448 → 22,144 (−304)
- **Notes:** 4-edit-site rule verified for ALL 5 tools (DEFINITIONS/GROUPS/DISPATCH stay; fn moves); dispatch-identity check all True. Matches git_tools.py pattern: local `_ok`/`_err`, brain runtime (`get_tool_config`/`AGENTS_DIR`/`_thread_local`) via lazy `import brain as _brain`. No config-loader duplication. No import cycle.

### 4 A3 git/github tools — DONE
- **Commit:** `3563081`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (Tier A pure win)
- **Symbol(s):** `tool_git_command`, `tool_github_command` + git-specific helpers `_run_git`, `_run_gh`
- **Moved FROM:** brain.py:16783–17165 (git/github subprocess-wrapper tools)
- **Moved TO:** engine/tools/git_tools.py (401 lines, NEW; engine/tools/ already existed w/ image_gen.py)
- **Old code deleted?** YES — Gate-2 grep: no `def tool_git_command`/`tool_github_command` in brain.py; alias + dispatch refs only.
- **Callers re-pointed:** 0 external (helpers had no outside callers); 2 dispatch entries via alias.
- **Tests:** Gate 4 imports 18/18 · Gate 5 80 pass / 3 known-NER fail · verdict PASS
- **Characterization test added?** n/a (Tier A)
- **brain.py delta:** 22,829 → 22,448 (−381)
- **Notes:** 4-edit-site rule verified; dispatch-identity True/True. Trivial `_ok`/`_err` re-declared locally per image_gen pattern (no brain runtime state needed). No top-level `import brain`. First entries under the new `engine/tools/` package convention (image_gen.py predates this refactor).

### 3 A2 code-structure graph — DONE
- **Commit:** `3aa1cf2`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (Tier A pure win)
- **Symbol(s):** `CodeGraph` (+ `_code_graph`), `CODE_GRAPH_DB`, `_code_graph_db_pool`, `_code_graph_conn`, `_code_graph_init_db`, `_EXT_TO_LANG`/`_DEFAULT_EXCLUDE_DIRS`/`_CLASS_TYPES`/`_FUNCTION_TYPES`/`_IMPORT_TYPES`/`_CALL_TYPES`, `_extract_node_name`/`_extract_call_name`/`_extract_import_name`/`_is_test_function`, `_get_code_graph`, `_maybe_update_code_graph`, `tool_code_graph_build`/`query`/`impact`/`enhance`
- **Moved FROM:** brain.py:16761–17931 (tree-sitter AST + code-graph.db subsystem)
- **Moved TO:** engine/code_graph.py (1205 lines, NEW module)
- **Old code deleted?** YES — Gate-2 grep shows no `def`/`class` of `CodeGraph` or `_maybe_update_code_graph` in brain.py; single `from engine.code_graph import (...)` alias block.
- **Callers re-pointed:** 0 external repoints — TOOL_DISPATCH ×4 + `_after_file_write`'s `_maybe_update_code_graph(path)` all resolve through the alias (identity-checked `brain.X is cg.X`).
- **Tests:** Gate 4 imports 18/18 clean · Gate 5 80 pass / 3 fail (only known NER-env) · verdict PASS
- **Characterization test added?** n/a (Tier A, self-contained; owns its own DB)
- **brain.py delta:** 23,982 → 22,829 lines (−1,153)
- **Notes:** Owns its `_code_graph_db_pool` (threading.local) — grep-verified NOT shared with any other subsystem, moved with the module. `_after_file_write` (general write hook: artifacts + code-graph + more) STAYED in brain.py, calls into engine via alias. **4-site tool-registration rule verified** (the v8.27.0 image_gen bug class): DEFINITIONS/GROUPS/DISPATCH entries stay in brain.py and resolve to the moved `tool_*` fns. tree-sitter import kept lazy/optional (not promoted to mandatory). No import cycle (sole brain-runtime touch was already a lazy `from handlers import sidecar_proxy`).

### 2 A1 workflow engine — DONE
- **Commit:** `094ec90`  ·  **Date:** 2026-05-23  ·  **Phase:** 1 (Tier A pure win)
- **Symbol(s):** `WorkflowError`, `_WF_KEYWORDS`/`_WF_TT`/`_WF_OP_MULTICHAR`, `_wf_tokenize`/`_wf_tok_line`, all `_WF*` AST nodes (`_WFNode`/`_WFLiteral`/`_WFVar`/`_WFGetAttr`/`_WFGetItem`/`_WFBinOp`/`_WFUnary`/`_WFFnCall`/`_WFList`/`_WFDict`/`_WFInterpStr`/`_WFAssign`/`_WFCall`/`_WFIf`/`_WFFor`/`_WFReturn`/`_WFProgram`), `_WFParser`, `_wf_parse`, `_WFReturnValue`, `_WorkflowInterpreter`
- **Moved FROM:** brain.py:12486–13443 (lexer→AST→parser→interpreter cluster)
- **Moved TO:** engine/workflow.py (977 lines, NEW module)
- **Old code deleted?** YES — Gate-2 grep shows no `def`/`class` of `_wf_parse`, `_WorkflowInterpreter`, `WorkflowError` in brain.py; brain.py keeps a single `from engine.workflow import (...)` alias block.
- **Callers re-pointed:** 5 in-brain.py sites (`WorkflowEngine`/`WorkflowExecution` methods + `WorkflowError` excepts) resolve via alias. No external callers.
- **Tests:** Gate 4 imports 18/18 clean · Gate 5 80 pass / 3 fail (only known NER-env) · verdict PASS
- **Characterization test added?** n/a (Tier A, self-contained pure engine)
- **brain.py delta:** 24,925 → 23,982 lines (−943)
- **Notes:** Boundary drawn between the **pure engine** (lexer/parser/interpreter — moved) and the **orchestration layer** (`WorkflowEngine`, `WorkflowExecution`, `workflow_*`/`_workflow_history_*` fns — STAYED in brain.py: entangled with `AGENTS_DIR`, `AgentConfig`, `_thread_local`, scheduler DB). Interpreter's only brain dependency is `TOOL_DISPATCH`, reached lazily via `import brain as _brain` inside `_eval_call` (established engine pattern; no top-level import → one-way DAG intact). Pure relocation, zero behavior change (runtime-verified: parse works, `WorkflowError` alias identity holds, TOOL_DISPATCH resolves 63 entries).

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

## Tier C eval baseline (the Δ<0.10 reference for C1/C2/C3)
- **Run:** `eval/results/20260523T142852_disc-none_tierC-baseline-v2/` · HEAD `3f87889` (post C2-chars-test, pre any Tier C extraction) · 2026-05-23.
- **Reused gold** from `20260515T175221_disc-none_gemma26b-loadaware-v2` (Opus gold is static — never re-run, per user rule). Brain side fresh on current config (Mistral Medium 3.5 via CLIProxyAPI); Mistral judge.
- **Brain mean = 0.77** (gold 0.93, Δ_brain−gold −0.17). Wins: gold 14 / brain 1.
- **Gate rule for each Tier C sub-step:** re-run eval (same `--skip-gold --reuse-results` source), brain mean must stay **≥ 0.67** (Δ < 0.10 below baseline). A drop ≥0.10 ⇒ revert that sub-step + log BLOCKED + surface. Mind the ±0.09 Mistral run-to-run variance — a borderline single point near 0.67 warrants a confirm re-run, not an instant revert.
- Per-sub-step extra gate: **C1** warmup system-prompt byte-identical (`_build_system_prompt` hash stable) ; **C3** project-wing isolation (`project__*` no leak).
