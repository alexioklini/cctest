# Refactoring Plan — Reusable Module Extraction

**Scope:** Identify *all* opportunities to refactor the codebase into reusable, single-responsibility modules.
**Date:** 2026-05-22
**Method:** Three parallel structural surveys (brain.py map, cross-file duplication, handler/server_lib/engine cohesion) + direct verification of line ranges and dependency direction.

> This is a **plan**, not executed work. Each item lists evidence, value, risk, and a verification gate. Nothing here changes behavior; every extraction is a move + re-export.

> **Cross-checked against an external code analysis (2026-05-23).** Its findings were verified accurate and folded in. Decisions taken: (a) **test gap** — add characterization tests for the specific path before each risky extraction (see §0.5); (b) **thread-locals** — B1 relocates only, full dependency-injection is explicitly a *separate future initiative* (see B1 + §8); (c) **~29k-line JS frontend** — explicitly **out of scope** for this refactor, tracked as a future initiative (see §8).

---

## 0. Ground truth (verified)

| Fact | Evidence |
|---|---|
| `brain.py` = 25,182 lines, the monolith. Lines ~1–560 are a single changelog string (skews any "section" line math). | `wc -l`; `grep "^class"` first class far below 560 |
| **Dependency DAG is clean & one-way.** `brain.py` imports *nothing* from `handlers/` or `server_lib/`. Handlers/server_lib import `brain as engine`. `server.py` imports all three. | `grep "^from handlers\|^from server_lib" brain.py` → empty |
| No circular imports anywhere. | cross-file import survey |
| Largest files: `brain.py` 25,182 · `server.py` 5,815 · `handlers/admin.py` 5,416 · `handlers/chat.py` 3,540 · `server_lib/db.py` 1,962 | `wc -l` |
| Per-dir docs already exist for `engine/` and `handlers/` (not `server_lib/`). | `ls */CLAUDE.md` |

**Architectural consequence:** Because the DAG is already acyclic and one-way, extraction is *low-risk by construction* — moving a self-contained block out of `brain.py` into a new module and re-importing it cannot create a cycle, as long as the new module doesn't import back from `brain.py`. The risk is concentrated in the *entangled* domains that share `_thread_local` and the DB pools.

---

## 1. The entanglement map (what makes extraction hard)

These shared globals are the seams that bind `brain.py` together. Any extraction must either (a) avoid touching them, or (b) move them into a shared `engine/context.py` that both the extracted module and `brain.py` import.

| Shared state | Spans | Implication for extraction |
|---|---|---|
| `_thread_local` (current_agent, mcp_manager, current_session_id, current_user_id, project, research_mode_override, caveman flags) | execution context, scheduler, task runner, tool dispatch, system-prompt build | **Central seam.** Extract this *first* into `engine/context.py` so other modules stop reaching into `brain._thread_local`. |
| `TOOL_DEFINITIONS` / `TOOL_GROUPS` / `TOOL_DISPATCH` | tool defs, resolver, dispatch, system prompt, settings UI | Registry referenced by 5+ domains; schema is canonical. Move the *data*, keep the *resolver* logic co-located. |
| `_provider_cache_lock`, `_key_pools_lock`, `resolve_provider_for_model` | provider routing, cost tracking, model selection, warmup | Single source of truth already; just relocate as a unit. |
| 8× `_*_db_pool` (sched, cost, traces, audit, code_graph, context, …) | scattered per-domain | Each pool belongs with its domain. Moving a domain moves its pool. |

---

## 1.5. Characterization tests — prerequisite for risky extractions

**The gap the external analysis caught:** the 1,923 test lines cover *only* the PII/pseudonymization/GDPR path. There are **zero tests** for tool execution, the chat worker loop, scheduler/task execution, session management, the sidecar protocol, or route handling. Our verification gate ("no new test failures") therefore **cannot detect a behavioral regression** in exactly the riskiest extractions (B2 scheduler, C2 tool-exec) — the gate would stay green while behavior silently changed. That is the precise failure mode that bit prior attempts.

**Decision (user-approved 2026-05-23): add characterization tests narrowly, just-in-time.** Not a broad test-coverage project — *behavior-pinning* tests for **only the path about to be extracted**, written and committed **immediately before** that extraction, so the gate becomes trustworthy where it matters.

| Before extracting | Add characterization tests pinning | Why |
|---|---|---|
| B2 scheduler | a fire of a scheduled task → produces the expected `schedule_history` row + synthetic session id + artifact scoping; `tool_profile` → purpose resolution | scheduler has zero tests; couples to `_thread_local` set per fire |
| C2 tool-exec | tool dispatch round-trip (dedup guard, artifact-session folder write, result summarization/truncation) | core path, untested; artifact-session coupling is subtle |
| C1 prompt/model (already eval-gated) | eval harness is the characterization here; **add** a byte-stable warmup-payload assertion | KV-prefix must not shift |
| C3 mempalace-glue (already wing-isolation-gated) | a test asserting `project__*` never resolves for a cross-project caller | security-critical isolation |

These tests are **part of the extraction's commit** (or the commit right before it) and become permanent regression coverage. Tier A extractions are self-contained enough that import-gate + existing tests suffice — no new tests required there.

---

## 2. Extraction candidates — `brain.py` (the 25k monolith)

Ordered by **value ÷ risk**. "Self-contained" = minimal `_thread_local` reliance; can move with a clean public surface.

### Tier A — High value, low risk (self-contained, do first)

| # | Domain | Approx. lines | Target module | Why low-risk |
|---|---|---|---|---|
| A1 | **Workflow engine** (lexer → AST → interpreter) | ~1,500 | `engine/workflow.py` | Pure compute; no `_thread_local`. Self-contained lexer/parser/interpreter. The single biggest clean win. |
| A2 | **Code structure graph** (tree-sitter indexing, `code-graph.db`, qualified names, edges) | ~1,200 | `engine/code_graph.py` | Owns its own DB pool; triggered via one hook (`_maybe_update_code_graph`). Clean boundary. |
| A3 | **Git / GitHub tools** (`tool_git_command` 18978, `tool_github_command` 19176) | ~390 | `engine/tools/git_tools.py` | Subprocess wrappers, no shared state. |
| A4 | **Gmail tools** (`tool_gmail_*` 4916–5200) | ~400 | `engine/tools/gmail_tools.py` | Self-contained API client tools. |
| A5 | **Trace manager & audit trail** | ~400 | `server_lib/trace_audit.py` (or `engine/`) | Observability only; owns `_traces_db_pool`/`_audit_db_pool`. |

### Tier B — High value, medium risk (touches shared seams)

| # | Domain | Approx. lines | Target module | Risk |
|---|---|---|---|---|
| B1 | **Relocate `_thread_local` + execution context** into `engine/context.py` | ~200 (the definitions) | `engine/context.py` | *Prerequisite* for clean Tier-B/C extractions. Many call sites read `engine._thread_local.*` — re-export keeps them working, but this is the load-bearing change. Do before B2–B4. **Scope note:** this is a *relocation only* — it does NOT convert the implicit-thread-local-dependency pattern to explicit dependency-passing. The external analysis correctly flags that thread-locals hurt testability ("Never fall back to globals — concurrent requests bleed"); full DI conversion is its own multi-week effort, tracked as a future initiative in §8, not part of this refactor. |
| B2 | **Scheduler + task runner** | ~1,640 | `engine/scheduler.py` | Tightly coupled to `_thread_local` (set per fire). Extract *after* B1. |
| B3 | **GDPR/PII scanner** (`_pii_rules` 16771 ~563 lines, `_pii_scan_text`, `_pii_scan_bare_identifiers`) | ~900 | `engine/pii_scan.py` (merge with existing `engine/pii_ner.py`) | Currently `engine/pii_ner.py` holds *only* the NER loader; the regex tiers + orchestration still live in `brain.py`. **Completing this extraction also addresses the web/index.html sync problem** (see §4). |
| B4 | **Quotas** (`QuotaManager`) + **cost tracking** (`CostTracker`) + **rate limiting** (`RateLimiter`) | ~600 | `engine/quotas.py`, `engine/cost.py` | Each owns a DB pool; called from chat round-0 gate. Medium coupling. |

### Tier C — High value, high risk (core agentic path — extract last, with eval gate)

| # | Domain | Approx. lines | Target module | Risk |
|---|---|---|---|---|
| C1 | **Model selection + system-prompt assembly** (`_build_system_prompt`, `MODEL_PROFILES`, thinking-level validation, research/plan mode wiring) | ~2,600 | `engine/prompt_build.py` + `engine/model_select.py` | This is the KV-cache-sensitive path. CLAUDE.md repeatedly warns the system prompt must stay user-agnostic and byte-stable for warmup. **Any change here must pass the eval harness + a warmup KV-prefix check.** |
| C2 | **Tool execution layer** (artifact-session handling, tool dedup, result summarization) | ~2,000 | `engine/tool_exec.py` | Couples to artifact session folders + `_thread_local`. Hard to isolate. |
| C3 | **MemPalace integration glue** (`tool_mempalace_query` ~535 lines, wing resolution, drawer/closet tools) | ~1,000 | `engine/mempalace_glue.py` | Force-scoping + visibility filter logic is security-sensitive (project isolation). Move as a unit, test wing-leak invariant. |

### Tier D — Complete the half-done extractions

CLAUDE.md says these are "partially extracted" — the `engine/*.py` file exists but `brain.py` still holds substantial logic:

| # | Domain | What's still in brain.py | Action |
|---|---|---|---|
| D1 | `engine/doc_convert.py` | Inline extraction helpers duplicated in `tool_read_document`, `extract_attachment_text`, classification scan | Route all callers through `convert_one()`; delete inline copies. *(Note: memory `project_doc_extraction_unified.md` claims this was unified in v9.10.0 — **verify current state before acting**; may already be done.)* |
| D2 | `engine/classification.py` | `_classification_gate_tool_text`, `_classification_effective_action`, `classification_pick_model_for_background` glue (~150 lines) | Move the enforcement glue next to the detector. |
| D3 | `engine/kg_extract.py` | entity indexing + co-occurrence logic in brain.py | Move into kg_extract. |
| D4 | `engine/pii_ner.py` → see B3 | full regex `_pii_rules` still in brain.py | Folded into B3. |

---

## 3. Extraction candidates — other oversized files

### `handlers/admin.py` (5,416 lines — 8+ unrelated concerns)

Split into an `handlers/admin/` package (mixin umbrella in `__init__.py`):

| Sub-module | Concern | ~lines |
|---|---|---|
| `admin/workflows.py` | workflow CRUD + run + history | ~900 — **isolated, extract first** |
| `admin/artifacts.py` | file preview/download/tree, artifact browse, channels, sidecar status | ~3,200 — large but cohesive (file ops) |
| `admin/costs.py` | cost breakdown, quota config, user limits | ~490 |
| `admin/skills.py` | CC skill zip install/removal/browse | ~300 |
| `admin/config.py` | tool settings, NER model load, research-mode disciplines | ~290 |
| `admin/teams.py` | team CRUD | ~200 |
| `admin/agents.py` | agent create/delete/rename | ~180 |
| `admin/observability.py` | KG extraction trigger, traces, audit | ~100 |

### `server.py` (5,815 lines — dispatch + daemons + init + sessions)

| Extract | Concern | ~lines | Keep in server.py |
|---|---|---|---|
| `server_daemons.py` | 7 background loops: mempalace miner/sync, project sync, user profile, warmup keeper | ~1,500 | HTTP dispatch (`BrainAgentHandler` at :982, do_GET/POST/…) |
| `server_init.py` (optional) | config load, auth/scheduler/tracing setup | ~600 | `Session`/`SessionManager`/`LiveStream` classes (core abstraction, leave) |
| `server_lib/mempalace_client.py` | `MemPalaceClient` singleton wrapper (server.py:69) — flagged by the external analysis | ~varies | — |

> **Extraction wrinkle (verified 2026-05-23):** the 7 daemon loops are **nested functions inside `def main()`** (defined at lines ~3903–5716, all within `main()` starting at 3033), not free functions. Extracting them means lifting them to module scope in `server_daemons.py` and passing what they currently close over (config, server globals) as explicit args. This is more than a copy-paste move — budget for it. Invariant #5 (thread-locals set before sidecar calls) applies.

### `handlers/chat.py` (3,540 lines — 3 concerns)

| Extract | Concern | ~lines |
|---|---|---|
| `server_lib/sse_stream.py` | SSE event formatting, 5s keepalive, replay buffer (reusable by any future SSE endpoint) | ~150 |
| `handlers/gdpr_recovery.py` | anonymization-modal state machine | ~80 |
| *(keep)* | HTTP → agentic dispatch (legitimately coupled to brain) | — |

### `server_lib/db.py` (1,962 lines — 3 concerns)

| Extract | Concern | ~lines |
|---|---|---|
| `server_lib/node_registry.py` | in-memory node config/commands | ~80 |
| `server_lib/mempalace_sync.py` | `chat_mempalace_sync` cursor helpers | ~100 |
| *(keep)* | `ChatDB` core session store | ~1,700 |

---

## 4. Cross-cutting reusable utilities (new shared modules)

These are duplicated patterns found across files — extracting them removes copy-paste drift. Propose a single `common.py` (or `server_lib/http_util.py` + `server_lib/pathsafe.py`).

| # | Pattern | Copies found | Target | Value |
|---|---|---|---|---|
| U1 | **Path-traversal guard** (realpath + deny `/etc /var /usr /bin /sbin /System /Library/Keychains` + allowlist) | classification.py:52-100, projects.py:375-399, plus inline copies in projects.py:328, favourites.py:612, admin.py:4887 | `server_lib/pathsafe.py: SafePathValidator` | **HIGH** — security code; divergent copies are a real risk |
| U2 | **HTTP body read** (`Content-Length` + `json.loads(rfile.read)`) | 16 sites across chat/translate/projects/classification/admin/favourites | `server_lib/http_util.py: read_request_body(handler)` | **HIGH** — inconsistent error handling today |
| U3 | **SSE event formatting** (`event: …\ndata: …\n\n`) | translate.py, chat.py, sidecar_proxy.py | folded into `server_lib/sse_stream.py` (§3) | MEDIUM |
| U4 | **Script/config dir** (`os.path.dirname(os.path.abspath(__file__))`) | ~82 occurrences | `common.py: REPO_ROOT` constant | LOW (cosmetic) |
| U5 | **PII scanner web/server sync** — `PIIScanner` in `web/index.html` mirrors `_pii_rules` in brain.py, "must stay in sync" by hand | 2 implementations | *Not directly mergeable* (browser is regex-only, JS). **Mitigation:** after B3, generate the JS rule table from the Python source (codegen) so they can't drift. | MEDIUM |

**Already centralized — do NOT re-extract** (agents confirmed these are fine): `_send_json`/`_read_json` (server.py base class), auth gates (`_require_role`), `resolve_provider_for_model`, `@_db_safe`/`_db_conn` (server_lib/db.py since v8.26.0), TOOL_DEFINITIONS dedup (v8.28.0). `handlers/favourites.py` vs `server_lib/favourites.py` is a legitimate HTTP/DB layer split, not duplication.

---

## 5. Recommended sequencing

Each phase ends with a **gate** before the next begins. Project rule (CLAUDE.md): commit directly to main, surgical changes, fail loud.

**Phase 0 — Safety net (do before any extraction).**
- Confirm the eval harness (`eval/`) runs green at current HEAD — it's the regression detector for Tier-C work.
- Add a smoke test: server boots, `/health` ok, one chat turn completes, one scheduled task fires. Refactors must keep this green.

**Phase 1 — Pure wins (Tier A + the two easy splits).** A1 workflow, A2 code-graph, A3 git, A4 gmail, A5 trace/audit; `admin/workflows.py`; `db.py` node-registry + mempalace-sync split. Each is a move + re-export. *Gate: smoke test + import check.*

**Phase 2 — Shared seam.** B1 (`engine/context.py` for `_thread_local`). Then U1/U2/U4 utility extractions (they unblock cleaner handler splits). *Gate: smoke test + grep that no module reads `brain._thread_local` directly anymore.*

**Phase 3 — Mid-risk domains.** B2 scheduler, B3 PII (+U5 codegen), B4 quotas/cost; `admin/` full split; `server_daemons.py`; chat.py SSE/GDPR split. *Gate: smoke test + a scheduled-task run + a PII-block test.*

**Phase 4 — Core path (eval-gated).** C1 prompt/model, C2 tool-exec, C3 mempalace-glue; complete D1–D3. *Gate: full eval harness within noise (Δ < 0.10) of pre-refactor baseline + warmup KV-prefix byte-identical check for C1.*

---

## 6. Invariants any extraction must preserve

1. **One-way DAG** — extracted modules must not import back from `brain.py` (would create a cycle). Use `engine/context.py` for shared state instead.
2. **Handler mixins resolve names from server.py globals** — re-export moved symbols (`from server_lib.chat_db import ChatDB`) so the mixin chain keeps resolving.
3. **System prompt stays user-agnostic & byte-stable** (warmup KV prefix). Gate C1 on a byte-identical warmup payload check.
4. **Project memory isolation** (`project__*` wings never leak) — gate C3 on the wing-visibility test.
5. **Thread-locals set before every background call** — daemons moved to `server_daemons.py` must still set `engine._thread_local.*` before calling the sidecar.
6. **Fail loud** — no silent skips; if a re-export breaks a caller, surface it.

---

## 7. Open questions to confirm before executing

- **D1 (doc_convert):** memory says unification shipped in v9.10.0 — is there still inline duplication in `brain.py`, or is this item already closed?
- **C1 scope:** `_build_system_prompt` is ~447 real lines but the surrounding model-selection cluster is ~2,600. How much moves together vs. stays?
- **`common.py` vs `server_lib/`:** prefer one new top-level `common.py`, or fold utilities into existing `server_lib/`? (Affects import churn.)
- **`admin/` package vs flat files:** package (`handlers/admin/__init__.py` umbrella) keeps the mixin import surface stable; confirm that's preferred over `admin_*.py` siblings.

---

## 8. Explicitly out of scope (tracked as separate future initiatives)

The external analysis (2026-05-23) raised three structural themes broader than module extraction. Captured here so they are **consciously deferred, not silently dropped** — each is its own project:

1. **Web frontend monolith (~29,000 lines vanilla JS, no framework/bundler).** Largest: `web/js/settings.js` (6,140), `panels.js` (5,819), `chat.js` (4,013), `init.js` (2,899). Same "monolith mid-decomposition" pattern as `brain.py`, but a different language, toolchain, and risk profile. **Not part of this Python refactor.** A future initiative would survey it (like we did `brain.py`), decide on ES-module/bundler adoption, and split the three big files. *Note: `web/index.html`'s `PIIScanner` IS touched indirectly by B3/U5 (codegen the JS rule table from Python) — that's the one frontend seam this refactor crosses.*

2. **Thread-local → explicit dependency injection.** B1 *relocates* `_thread_local`; it does not eliminate the implicit-dependency pattern the analysis criticizes (every function depends on setup done elsewhere; "concurrent requests bleed" if a fallback-to-global slips in). Converting reads to explicit context-passing — following the model the sidecar already uses (context dict echoed back) — is a multi-week architectural effort across the whole codebase. **Deferred.** Could be piloted on one domain (e.g. scheduler) later to prove the pattern before committing broadly.

3. **Institutional knowledge in prose, not enforced invariants.** The CLAUDE.md / handover docs are accurate but carry invariants (KV-prefix stability, wing isolation, 4-edit-site tool rule, thread-local discipline) that exist only as prose. A future initiative could encode the load-bearing ones as **tests or assertions** so they fail loudly when violated. The characterization tests in §1.5 are a first down-payment on this (warmup-payload assertion, wing-isolation test); broadening it is out of scope here.
