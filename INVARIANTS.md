# INVARIANTS.md

Deep subsystem invariants. Referenced from `CLAUDE.md`. **Non-obvious invariants only** — factual catalogs (tool list, endpoints, config fields) live in the code; grep/read it.

## GDPR / PII Pre-Submit Scanner

71 regex detectors, client + server, zero external APIs.

**NER layer (v9.4.0)**: spaCy German `de_core_news_md` (~120MB) eager at startup adds 3 rule_ids: `name`/`address`/`organisation` (all `contact` category, default `ignore`). Runs in `_pii_scan_text` AFTER regex + bare-id (regex wins on overlap). Shape gate `_passes_shape_gate` drops FPs: lowercase entities, digits-only, `_ORG_ACRONYM_BLOCKLIST` (DSGVO/IBAN/BGB). No `ner_enabled` switch — category action controls surfacing. Runtime: Settings → GDPR per-language pill via `GET/POST /v1/gdpr/ner-models` (admin, audited). Browser scanner stays regex-only. German only (lang hard-coded); Phase 2 = EN+RU + detection. FP upgrade: `de_core_news_lg`/`de_dep_news_trf`.

**Two mirrored regex impls**: `PIIScanner` (`web/js/utils.js`); `_pii_rules()`+`_pii_scan_text()`+`_pii_scan_bare_identifiers()`+`PII_RULE_CATEGORIES`+`PII_DEFAULT_CATEGORY_ACTIONS` (`engine/pii_ner.py`, re-exported). Regex scanner pure (lazy-imports brain only for config-action resolver). **Rule order in `_pii_rules` is a correctness invariant — never reorder.** Py↔JS parity auto-checked by `tools/check_pii_js_parity.py` (category/action maps + rule_ids; `refactor_gate.sh` Gate 4b); regex bodies differ by dialect, not checked.

**Three tiers** (first-match-wins, overlap suppression): cloud secrets/keys → national IDs w/ checksums (~30 countries) → context-fallback + bare-id heuristic. **Rule-order invariants**: context-gated (DE Steuer-ID, NL BSN, HU TAJ) before generic bare-digit; `credit_card` AFTER all national-ID checksums (RO CNP/KR RRN are 13-digit Luhn-passing); `phone` AFTER national IDs (XXX-XXX-XXXX SIN/NHS would steal slot); `credit_card` has `(?<![+\d])` so `+CC` phone prefixes don't match. **Overlap suppression**: successful matches claim spans, failed validations DON'T (weaker rules re-scan inside invalid IBAN — why Aadhaar/PESEL/Steuer-ID are context-gated).

**Routing**: hard-block raises pre-LLM **only when model non-local** (local bypasses, data on-prem). `gdpr_pick_model_for_background(model, texts, purpose)` = **single decision point** for non-interactive: scan → audit `pii_detected` → swap to fallback if configured (`pii_auto_fallback`) → else `server_block` raises `GDPRBlockedError` (`pii_blocked`) → else warn. Client `piiBlockActive(chat)` filters dropdown to local-only when `server_block` + scanner on + PII present; auto-swaps via `piiEnsureLocalModel()`. `is_model_local()` → `_is_local_base_url()` matches localhost/127/0.0/RFC1918.

**Config** (`gdpr_scanner`): master toggle, `server_log`, `server_block`, `default_local_fallback_model`, 8 per-category actions, `rule_overrides`, `email_allowlist`. `block`→`warn` when `server_block` off. **Not detected**: personal names, addresses, ICD codes, generic passport/license without context.

**Web-Egress-Gate (v9.334.0, L4 Phase 1)**: `brain._gdpr_guard_web_args(tool_name, args)`, called at the ONE live dispatch choke point (`engine/llm_loop.py:dispatch_tool`, before TOOL_DISPATCH lookup) — covers interactive, background AND scheduler turns. Active only when `get_request_context()._gdpr_mapping_id` is set (anonymising sessions); everything else untouched. Checks ALL string args recursively (web_fetch: also the URL) against the session's **known protected values** — `mapping.forward` (originals), `mapping.reverse` (fakes/tokens), `pii_decisions` ledger (FP values exempt) — with lowercase + URL-slug normalisation (space→`-`/`_`/`+`/`%20`, first/last-token pair for multi-word names). **NOT findings-driven**: with `contact=ignore` the name is neither in the mapping nor actionable — the gate asks "what IS PII" via a fresh scan with gate-own category policy (`_WEB_GATE_PASS_CATEGORIES = {business_id, network}` pass so technical queries never trip; rule_overrides dropped; user-declared FP values suppress fresh findings). **Invariants**: (1) fakes → ALWAYS refuse, in every mode (fake search = semantically empty or poisons evidence with strangers' data); (2) error `web_query_blocked_pii` carries `value_kind`, NEVER the value (it goes back to the model); (3) fail-CLOSED on gate crash while a mapping is active; (4) web tools must NEVER appear in any future args-deanonymisation whitelist (L3) — that would be silent egress. Modes `gdpr_scanner.web_egress`: `refuse` (default) | `ask` (behaves like refuse until L4 Phase 2 / `release_web` lands) | `block_group` (chat worker additionally excludes `WEB_SEARCH_TOOLS` via exclude_tools; dispatch gate stays as defense in depth) | `allow` (originals pass audited, fakes still refused). Audit: `pii_web_blocked`/`pii_web_egress` (kinds+mode only). Clamp: `_GDPR_ANON_CLAMP` instructs "nicht prüfbar (Datenschutz)", never "no results" for a refused search. Tests: `tests/test_web_egress_gate.py`.

**Dispatch symmetry (v9.336.0, L3)**: the model thinks in fakes, the tools work on raw data — neither knows about the other.
- **L3a args-deanonymisation**: `brain._gdpr_deanon_tool_args(tool_name, args)` at the dispatch choke point (`engine/llm_loop.py:dispatch_tool`), **AFTER** the web gate (order is an invariant — the gate judges the MODEL's args, never back-translated ones). Recursively translates fakes/tokens → originals, only for `GDPR_ARGS_DEANON_TOOLS` (mempalace_query, mempalace_kg_*, read_document, read_file, list_directory, search_files, execute_command, python_exec, ocr_*, xlsx_*, text_diff, doc_checks tools). **Web tools are NEVER whitelisted** (silent egress) — guarded by `tests/test_dispatch_symmetry.py` (whitelist ∩ `WEB_SEARCH_TOOLS` = ∅). Returns a NEW structure; wire/history keep the fakes. Fail direction: tool runs with fakes (may miss, can never leak). Hardening beyond the handover: `execute_command`/`python_exec` strings containing network markers (`_DEANON_NETWORK_MARKER_RE`: curl/wget/https:///urllib/requests/…) keep their fakes — a shell/script can reach the net itself, deanonymising there would be egress through a side door.
- **L3b results-anonymisation** (per-tool, NEVER a generic post-hook — double-anon risk): `tool_mempalace_query` + `tool_mempalace_kg_query/search/neighbors` (v9.96.0 "stay raw" decision REVERSED) and web-inbound (`tool_web_fetch` via `_web_result_anon` wrapper — content field only, covers cache/academic/YouTube/audio/file paths; `_searxng_query` both content returns → all 5 searxng tools; `exa_search`) now pass `_gdpr_anon_tool_text`. Side effects (intended): the classification gate now also covers mempalace drawers + web content (daemon callers are safe — no session model → gate no-ops); the Websuche basket prefetch (`_build_web_sources`) inherits the seam (F5 partly closed) and catches `GDPRBlockedError` per source. Pseudonymised `read_path`s round-trip via L3a on the follow-up `read_document`.
- **L3c ledger-rewrite notice split**: `_apply_pii_decisions_to_wire` applies `_split_attachment_notice` like the scan path — the ledger replace must never rewrite values inside attachment-notice disk paths (breaks follow-up reads).

**Entity-consistent pseudonymisation (v9.337.0, L2)**: the mapping works on ENTITY level, not string level — ONE fake identity per person, every surface form maps to the FORM-MATCHING variant of the same fake.
- **Bookkeeping vs working memory**: `Mapping.entities` (`pseudonymizer.py`; {sur, givens, fake_sur, fake_givens}; encrypted-persisted, legacy rows load as `{}`) is the bookkeeping; the `forward`/`reverse` string tables stay the working memory. **Predictable variant PAIRS are registered as REAL forward/reverse entries** (`_register_entity_variants` ← `identity.standard_variant_pairs`: order/comma/initials/ALLCAPS/MRZ-name-form/glued-givens/surname) — that is what makes the L3a args-deanon and the web-egress gate entity-aware with ZERO code change there (handover §7.9). Matching/rendering logic lives in `engine/identity.py` (shared with L1 doc_checks — extend there, don't duplicate): `entity_attach` (3 tiers: names_match ≥0.84 → initial-tolerant → garble rescue `GARBLE_FLOOR=0.60`/`GARBLE_ANCHOR=0.72`, every token must bind a DISTINCT entity token), `render_variant` (token-wise, separators/digits/titles verbatim, case + initial style preserved).
- **Seeding order invariant**: `_seed_entities_in_text_order` runs BEFORE the end-descending splice pass — real documents put the clean scan first and OCR-garble duplicates last; splice order would seed the entity from the worst garble (measured on the reference JPG set). Garbage guard: implausible name forms (non-name tokens, >3 givens) never create entities and never teach the learn path — they fall back to the plain `_fake_name` shape fake.
- **Passport/MRZ (L2b)**: `passport`/`passport_ctx_loose`/`mrz`/`dob` joined `SHAPE_PRESERVING`. Keyword spans keep the keyword, only the value is faked. Bare passport number + 10-char number-with-check form are registered as own entries → VIZ and MRZ carry the SAME fake. New scanner rule `mrz` (line-start anchored, structural validator, checksum trust tier; deliberately NO `$` anchor — real OCR lines carry trailing garble). `_fake_mrz` rebuilds the line consistently: fake number, DOB shifted by the session offset, **expiry UNCHANGED** (document-lifecycle), nationality/sex verbatim, ALL ICAO-9303 check digits (incl. composite) recomputed via the doc_checks calculator — the LLM's own MRZ math verifies again (F2). An opaque token claimed by another rule (cz_rc matches bare 9-digit numbers) is NEVER spliced into an MRZ line.
- **Date offset (L2c)**: `_fake_date` applies a CONSTANT salt-derived per-mapping offset (`date_offset_days`, ±5..25, real calendar arithmetic) instead of per-value day jitter → ordering, deltas ("10y − 1d"), renewal gaps, EXIF distances stay EXACT. Formats (L2d) include textual months EN/DE + EXIF (`_DATE_PATTERNS` AND the scanner `date`/`dob` rules — else the same date lives in two truths). Unparseable → opaque token (the old bare-year fallback would let `reverse['1947']` rewrite every year occurrence).
- **Known-values sweep**: `apply_known_values` (word-bounded, exact registered names/emails ≥4 chars) runs in `_gdpr_anon_tool_text` after the scan pass — the German spaCy NER regularly misses English names in mempalace drawers/web results. Word boundary keeps paths intact (`STARK_Bonnie…` untouched — path integrity is L3a's job) and compounds safe (`Starkstrom`). Audit event carries `known_values_swept`.
- Tests: `tests/test_pseudonymizer_entities.py`. Known residue: extreme OCR garble that shares no recognisable token (`SOSTARKT`, `BONNT DCMARTE` standalone) is not attached at string level — that is the L5b MRZ-entity-seed's job; lone given names are deliberately never registered as variants (too FP-prone).

## doc_checks — deterministic document verification (v9.335.0, L1 PII-parity)

`engine/tools/doc_checks.py` (`mrz_verify`/`doc_dates_check`/`identity_consistency`) + `engine/identity.py` (name normalisation — SHARED with the L2 entity layer since v9.337.0; extend there, don't duplicate). Pattern: model supplies INTENT, server computes on RAW files, verdicts are PII-free → identical output with GDPR scanner on/off, immune to pseudonymised values.

- **Honesty invariants**: an unreadable MRZ field yields checksum `null`, NEVER `false` (an OCR garble must not read as forgery evidence — the F2 failure in reverse); `all_valid` requires ≥3 checkable digits, else `partial: true` + note. DOB values never leave a tool raw (age/equality only); document-lifecycle dates (issue/expiry) may (low identification power, mirrors the L2c decision).
- **MRZ OCR**: the generic full-page pass yields ZERO parseable data lines on the real reference photos — `_ocr_mrz_strip` (tesseract, char whitelist `A-Z0-9<`, bottom-strip + full-frame crops, psm 6) is load-bearing. Checksums SELF-VALIDATE the best reading (strip → generic → vision model); a full strip hit skips the expensive reads.
- **Date deltas are CALENDAR-exact** (`_human_delta_dates`): 2017-01-27→2027-01-26 is "10y - 1d"; a days//365 approximation says "10y + 1d" = false forgery suspicion on a regular 10-year passport.
- **Name matching is conservative by design** (FUZZY_THRESHOLD 0.84, initials never carry alone, single surname never matches, glued-token fallback only on ≥10-char tokens): a false MERGE of two real persons is worse than a miss. Calibrate against the real 10-JPG set (`/tmp/brain-attachments/58e3c521438a/`), never synthetic scans — the v9.329 lesson.

## Document Classification — ARL 20.02.02.06 (v9.6.0)

Phase A = audit Data view; Phase B = enforcement at GDPR seams. Shipped together v9.6.0.

- **Detector** (`engine/classification.py`): pure `detect_classification(text, *, filename, page_texts, cfg, pii_findings)`. Three signals, structural-first: (1) **Marker regex** — `Dokumentenklassifizierung … <level>`, `Classification:`, TLP `RED/AMBER/GREEN/WHITE`, filename hints (`*vertraulich*`, ARL `20.\d{2}\.\d{2}`); per-page scan feeds `confidence`. (2) **Filename hint** — only when body has no marker. (3) **Content heuristic** — PII + `classification.keywords` → `heuristic_level`. Mismatch fires when `heuristic_rank > marker_rank` (HIGH when marker=public + PII/confidential keywords, or delta ≥2). Over-classification is fine (§1.5). **Unmarked is first-class**, not auto-promoted.
- **Endpoints** (`handlers/classification.py`): `POST /v1/classification/scan-{files,folder,project}`; `GET /scans[/<id>[.csv]]`; `DELETE /<id>`; admin `GET/POST /config`.
- **Text extraction**: reuses `doc_convert.convert_one()`. `.md/.txt/.html/.csv` direct. Page-split via form-feed or `--- page N ---`.
- **Path guard**: realpath-resolved, under repo root/`agents/`/cwd/visible `input_folders[]`. Hard-deny `/etc /var /usr /bin /sbin /System /Library/Keychains`. Cap 500/scan.
- **Persistence** (`classification_scans` in chats.db): `scan_id + user_id + summary_json + evidence_json` (50KB cap, progressive trim). Non-admins see own only.
- **UI** (`classification.js`, `#data-view`): 3 input modes, filtered table, CSV export, history, drag-drop. **Settings tab** (`settings_general_tabs.js _genTab_classification`; handlers `settings_tools.js`): admin keyword lists per sensitivity w/ restore-defaults + extra regex. WPB `DEFAULT_KEYWORDS` (Vorstand, Aufsichtsrat, CISO, CRYPTSHARE).

**Phase B enforcement**:
- **Policy** (`config.json → classification_scanner`): `{enabled, server_block, server_log, default_local_fallback_model, per_level_action: {public:ignore, internal:warn, confidential:force_local, strict:block, unmarked:warn}}`. Defaults `_CLASSIFICATION_DEFAULTS`. `_classification_effective_action(level, cfg)` has **strict-always-block invariant** (§1.11): `strict` always `block` (or `force_local` when `server_block=False`).
- **`brain.ClassificationBlockedError`** subclasses `GDPRBlockedError` → every `except GDPRBlockedError:` site (10+) picks it up free.
- **`classification_pick_model_for_background(...)`** parallels GDPR's (no anonymise — stripping PII doesn't change legal classification). Audit: `classification_detected`/`_auto_fallback`/`_blocked`.
- **Single seam**: `gdpr_pick_model_for_background` calls classification FIRST → every GDPR site obeys classification, zero extra wrapping.
- **Tool-read gate** (`_classification_gate_tool_text` inside `_gdpr_anon_tool_text`): read_document/read_file/python_exec/execute_command output above threshold + non-local model → raises `ClassificationBlockedError` (dispatcher → JSON tool-error). Fail-open on errors. Only block fires here; force_local enforced at composer pre-flight (model already locked).
- **PDF footer fallback** (`extract_pdf_page_texts`): markitdown yields no marker → `detect_with_pii(text, pdf_path=path)` triggers fitz per-page, re-scans markers only. WPB ARL footer is vector graphics → unrecoverable.
- **Composer modal** (`panels_gdpr.js: classificationActionModal`): after PII modal in `sendMessage()`. Strict+block → Cancel only. Confidential force_local → Cancel + local-model. Skipped when local. `classificationBlockActive` folded into `piiBlockActive`.
- **`/v1/attachments/scan`** gains `classification: {marker_level, final_level, marker_meta, marker_evidence, mismatch, effective_action, level_label_de}`. Chip badges (`files.js`): 🔒/🏠/⛔ + label.

**Phase C (deferred)**: derived-artifact auto-marking (needs session taint + per-format injection); soul-chat/workflow/warmup not yet wrapped (same 3/22-site gap as GDPR); Telegram out of scope.

## Tools — Per-tool settings & dispatch

Source of truth: `TOOL_DEFINITIONS` (`engine/tool_schemas.py`, Anthropic flat shape, re-exported on brain). Impls in `engine/tools/*` + `engine/mempalace_glue.py`; wiring (`TOOL_GROUPS`, `TOOL_DISPATCH`) in `brain.py`. Groups: core, documents, code_graph, web, email, delegation, git, scheduler, mcp, skills, nodes, context, memory, code_exec. Per-turn resolution: `resolve_active_tools(purpose=...)` — single decision point (chat, scheduler, warmup, background, settings UI).

**Dispatch path**: sidecar `tool_use` → POSTs Brain `/v1/tools/call` (nonce-protected via `sidecar.tool_endpoint_internal`, localhost-only) → `tool_mcp.handle_tools_call` validates nonce, rebuilds context (current_agent, mcp_manager, current_session_id, current_user_id, project), dispatches to `engine.TOOL_DISPATCH` (or MCP fallback), captures result via `sidecar_proxy.capture_tool_result(turn_id, tool_use_id, ...)` for the `tool_dispatch_done` SSE → returns result string. **Synchronous by design** — `handle_tools_call` returns before the proxy drains `tool_dispatch_done`; don't make async without rethinking the result-capture handoff.

### Per-tool settings (admin-editable, global)

`config.json → tool_settings`, keyed by tool name, loaded into `engine._tool_settings` + mirrored to `server_config`. Per-record schema:
- `enabled` (default True) — global kill switch; false hides from every agent unless overridden. Server-internal callers unaffected.
- `deferred` (default False) — hide from initial list, expose via `tool_search` only.
- `purposes` (default []) — allowed purposes (`interactive`/`transform`/`memory_summary`/`research_minimal`); empty = all. Seeded from current behavior.
- `description`/`when_to_use`/`warnings`/`examples` (prose, empty=omitted) — injected into system prompt under `## <tool> / ### <Section>` by `_render_tool_descriptions`.
- `applies_with` (list) — all-of gate; prose renders only when every listed tool is also active.

Adding a prose record never hides/defers the tool. Renderer skips `enabled=false` defensively.

**Resolution hierarchy** (per LLM call): `effective_enabled/deferred = global`; if agent_id, `tool_overrides.<name>` overrides enabled/deferred (if present); drop if `not enabled`; drop if `call.purpose not in global.purposes` (when set); if deferred and not in discovered_tools → drop (surface via tool_search). **Purpose layer is global-only** — agents can't override (purpose is a property of the call). Scheduled tasks use same hierarchy; purpose from `tool_profile` (`""`→`research_minimal`, `"interactive"`→`interactive`) or name prefix (`_memory_summary_*`→`memory_summary`).

**Endpoints** (admin-only): `GET /v1/tools/settings` (all 64 tools + group + purposes), `POST /v1/tools/settings` (one record, validates name/applies_with/purposes, audited `tool_settings_save`), `GET /v1/tools/breakdown?agent=<id>` (per-tool token cost: name/description/schema), `GET/POST /v1/research-mode/disciplines` (refusal/precision/citation strings for research-mode project chats; per-section opt-out via empty string; audited `research_mode_disciplines_save`).

Legacy `tools.md` is gone — anchored blocks one-shot migrated into `tool_settings` on first post-migration startup (`migrate_tool_settings_from_md`).

**Project-flow text** (3-step retrieval, `read_path` how-to, KG decision rule, BINARY DOCUMENTS note) lives in per-tool descriptions of `mempalace_query`/`read_document`/`mempalace_kg_search`/`mempalace_kg_query` — NOT in `_build_system_prompt`. KG + read_document descriptions carry `applies_with: ["mempalace_query"]` so they only render in project-retrieval. Brain emits only a short "project chat with own memory" paragraph.

**Admin UI** (`settings_tools.js`, Settings → Tools): grouped registry of all 64 tools. Per-tool panel: enabled/deferred toggles, group label, integration knobs (~13 tools w/ `tool_config`), 4 prose textareas, applies_with multi-select. Two saves: `Save` → `/v1/tools/settings`, `Save integration` → `/v1/tools/config`.

**Topic A/B split** (v9.0.x): Topic A = retrieval discipline (search-first, query shape, saving) in `tool_settings.mempalace_query.description`, renders for every chat w/ the tool, admin-editable. Topic B = output-format discipline (refuse-on-empty, precision, per-claim citation) in `DEFAULT_PROJECT_INSTRUCTIONS` constant, renders only project + research_mode, hardcoded.

**Constraints**:
- `execute_command`: no TTY/stdin, `TERM=dumb`. Banned commands in its description.
- Memory is MemPalace **direct, not MCP**: `mempalace_query` (+ `save_chat_to_memory`, `mempalace_get_drawer`, `mempalace_list_drawers`).
- **Adding a tool** = 4 sites / 3 files: schema in `TOOL_DEFINITIONS` (`engine/tool_schemas.py`), `TOOL_GROUPS` (`brain.py`), the `tool_*` fn (`engine/tools/<group>.py`, reaches brain via lazy `import brain as _brain`), `TOOL_DISPATCH` entry (`brain.py`). **Dispatch-identity rule**: `TOOL_DISPATCH` value must be a direct fn ref, not a `lambda args: tool_X(args)` forwarder, or the 4-site checks fail. Prose added later via UI.
- **Pre-existing bug**: 4 tools (`memory_delete`/`memory_recall`/`memory_shared`/`memory_persist`) missing from `TOOL_GROUPS` → surface as `(ungrouped)`.

## Projects & Project Mode

`ProjectManager` CRUD; `instructions` in `project.json` injected into prompt; multipart upload to `IngestManager`.
- **Project ID**: `id` = uuid4 hex[:12], assigned on first read. **MemPalace wing key** — renaming doesn't strand drawers. `create_project` mints upfront; backfilled lazily for legacy.
- Archive: `status: "archived"` (files kept). Delete: soft to `.trash/`.
- **Notes**: AI editing uses `write_file`/`edit_file`. Note-AI sessions `status: note_chat`, hidden from chat list.

### Project Mode: `research_mode` (v8.31.0, split v9.0.x)

`project.json.research_mode` (bool) gates output-format discipline (Topic B) independently of `instructions`. Per-session `sessions.research_mode_override` (sticky NULL/0/1) layers on top. Effective = `override if not None else project.research_mode`. Resolution in `_build_system_prompt` (sets `research_mode_override` so `handlers/chat.py` gates citation validator/re-round consistently).

**Topic A/B split**: Topic A (search-first, query discipline, 3-step retrieval flow, `read_path`/`.md`/BINARY DOCUMENTS, KG decision rule) lives in `tool_settings.{mempalace_query,read_document,mempalace_kg_search,mempalace_kg_query}.description`, gated by tool presence + `applies_with: ["mempalace_query"]` (project chats only), admin-editable. Topic B (REFUSAL/PRECISION/CITATION) lives in `config.json → research_mode_disciplines` (admin-editable, `GET/POST /v1/research-mode/disciplines`, per-section opt-out via empty string, defaults `RESEARCH_MODE_DISCIPLINE_DEFAULTS`), gated by project + research_mode.

**Mode ON** (Q&A/policy/compliance): soft `PROJECT MEMORY` block ("MUST consult memory tools first"); detailed flow via tool descriptions; 3 discipline sections injected; server-side citation validator + synchronous re-round on violation (>30% uncited or ≥2 unverified quotes), gated by `mempalace.citation_reround.enabled`.

**Mode OFF** (codegen/drafting): soft `PROJECT MEMORY` ("use mempalace_query when relevant"); Topic A tool descriptions still render; `research_mode_disciplines` NOT injected (model falls back on training); validator + re-round skipped.

- **Owner `instructions`** is purely additive in both modes (appended verbatim). Never a fallback for disciplines (that was v8.23, replaced).
- **Legacy migration**: `_project_research_mode(cfg)` — absent `research_mode` field: empty `instructions` → True; non-empty → False.
- **Composer button** (`btn-research-mode`, hidden non-project): two-state cycle project default ↔ override-opposite; sticky like `save_to_memory`.
- **Infrastructure facts** (3-step flow body, BINARY DOCUMENTS, `read_path` vs `read_path_original`) STAY in system prompt regardless of mode.
- `DEFAULT_PROJECT_INSTRUCTIONS` constant = Topic-B text, injected only when research_mode on.
- **Anti-room-guessing**: `mempalace_query` `room` param enumerates real rooms (`general`/`artifacts`/`chat`/`chat_summary`/`chat_attachment`/`reference`) and forbids invention.
- **Sampling for Mistral Small** (gitignored): `temperature: 0.2`, `top_p: 0.85`. `0` rejected by provider; `0.1` no improvement.

### Token Optimisations
- **Per-session project preamble**: dynamic project state out of `_build_system_prompt` into per-session preamble at round 0 (`_project_preamble_text`). KV-prefix stays project-agnostic; ~1KB saved on no-cache providers.
- **Cross-turn discipline**: tool_use/tool_result blocks never persisted to `session.messages` — only user msgs + final assistant text survive. 2nd-turn re-read hits disk; within-turn `tool_dedup` kills double-reads. The old per-session `_read_doc_cache` stub-returner removed v9.7.0 (fired when turn-1 reply lacked needed content; stub's "use previous tool_result" was a cross-turn lie). Citation validator's read-path lookup preserved via `_session_read_paths[sid]: set[str]` (`_record_session_read_path`; public helper kept old name `_read_doc_cache_session_paths`).

### Project Input Folders + Sync
On-disk folders mined into project's private MemPalace wing (with `ingested/` manual attachments = project memory).
- **Schema** (`project.json`): `input_folders: [{path, recursive, auto_sync, added_at}]`, `sync_status: {state, last_run_*, total_indexed, total_files, total_triples, items}`. Live snapshot `_project_sync_live[(agent, project)]`.
- **Path guard**: realpath-resolved; refuses `agents/`, `/etc /var /usr /bin /sbin /System /Library/Keychains`.
- **Daemon** (`mempalace-project-sync`, 6h default): ensure `mempalace.yaml` matches wing (auto-rewrite), mine `ingested/` then each folder. Per-folder cap 5000. `total_indexed` cumulative. **Single-threaded** — multi-project cycles strictly sequential. `auto_sync=false` skipped on scheduled cycles, bypassed for manual "Sync now".
- **Startup wipe**: drops every `project__*` drawer + clears all `sync_status`. Runs every restart — needs marker-file gate (backlog).
- **System prompt** when `project` set: prepends PROJECT MEMORY + PROJECT INPUT FOLDERS blocks (absolute paths + path-join example to resolve `source_file` before read).

## Project Knowledge Graph

LLM-driven document → triples over project input folders + attachments. Post-pass after drawer mining; writes to `<palace_path>/knowledge_graph.sqlite3`.
- `kg_extract.py` — Profiles: `normative` (policies/regs/SOPs), `generic` (prose). Chunking: **`source_file`** (default) re-chunks original markdown at `source_chunk_chars` (3500) — ~70× yield. `inference_max_tokens=8000` (reasoning models exhaust mid-JSON below).
- `doc_convert.py` — pre-mine binary→companion `.md` under `<folder>/.brain-extracted/<name>.<ext>.md`. Idempotent via `(mtime,size)` frontmatter. `<!-- brain-source: <abs> -->` resolves back to original.
- **`normative` vocabulary**: 12 controlled English predicates (requires, forbids, permits, defines, cites, applies_to, effective_from, supersedes, responsible_party, condition, exception, penalty). Subjects/objects verbatim in source language. Off-vocab allowed.
- **Daemon hook**: `_run_kg_for(...)` resolves prefix via `os.path.realpath()` (macOS `/tmp`→`/private/tmp`, else source_files don't match).
- **Closet regen** (optional): replaces regex closets w/ LLM topic lines. Incremental via `closet_regen_progress` cursor on `(mtime,size)`.
- **Source-change invalidation**: snapshots `kg_extraction_source_state`; on diff DELETEs `triples` matching **exact** `source_file` + progress rows + orphan-entity sweep.
- **Agent KG tools**: auto-scope to caller's project via `project`; refused outside project context.

**Known constraints**: (1) GPU: 26B warmpool (~22GB) + e4b extraction (~5GB) exceeds oMLX 25.6GB — raise cap, or 26B `warmup:false` + `max_concurrent:1`, or cloud. (2) MemPalace KG 3.3.0 lacks `source_drawer_id`+`adapter_name` → `kg_extract` falls back via `TypeError` to legacy 5-arg `add_triple`. (3) KG path is `<palace_path>/knowledge_graph.sqlite3`, NOT `~/.mempalace/...`. (4) Code skipped (`_is_code_path()` → folded into Brain's code graph).

## Per-User Account Settings

`users.preferences` JSON on `auth.db` (validated via `PREFERENCE_DEFAULTS` + `_coerce_pref`):
- `greeting_name` (≤64), `job_description` (≤500), `communication_preferences` (≤4000) — in first-turn preamble
- `memory_chats_default` (0|1|2|null) — overrides classifier `default_mode`
- `memory_sched_default` (0|1|null) — gates miner from user's sched artifacts
- `daily_summary_enabled` (bool), `daily_summary_hour_local` (0-23)

`update_preferences` is merge-update w/ atomic validation; default-valued keys pruned. `update_user` (admin) doesn't touch this column.

**First-turn preamble**: prepends `[Context about this user: …]`. **Kept OUT of system prompt** (injecting greeting broke warm-pool KV-prefix; reverted). Stripped by `_ALLOWED_MSG_KEYS`.
**Refinement**: `/v1/refine` `purpose: "profile_field"` polishes (preserves first-person). `mistral-vibe-cli-fast` works; gemini-2.5-flash silently echoes input (model bug).
**My Schedules**: non-admins see own only; legacy (empty `user_id`) admin-only. `_schedule_owner_check(name)` gates mutations.

## User Profile (Memory from chat history)

Auto-maintained per-user context profile, mirrored to MemPalace.
- **Storage**: `agents/main/user_profiles/<uid>.md` (FS source of truth, gitignored) + `<uid>.history/<ISO>.md` (capped 30, KEPT on Reset). MemPalace mirror: `wing=user__<uid>, room=user_profile`, drawer per `## section`, purge-then-add.
- **Schema**: fixed sections (Work/Personal context, Top of mind, Recent months, Earlier context, Long-term background). `_PROFILE_SYSTEM_PROMPT`: never invent, third-person, match user's language, edit in place, demote stale Top→Recent, no timestamps, 2-6 sentences/section.
- **Daemon** (`user-profile`, 30min): gate = `daily_summary_enabled` + local hour match + 23h cooldown (`auth.db.user_daily_summary`).
- **Worker** (`_profile_run_synchronous`): 100 most-recent chats from 90 days, `background_call` w/ `_PROFILE_SYSTEM_PROMPT`, model `_profile_pick_model` (refinement → cheapest haiku → cheapest enabled → default). GDPR auto-fallback to local. Atomic write tmp + `os.replace`.
- **Preamble**: round 0 reads `<uid>.md` (4KB cap), prepends `[Auto-maintained user profile: …]`. Stripped by `_ALLOWED_MSG_KEYS`.
- **KV-cache invariant**: `_build_system_prompt` stays user-agnostic; per-user content ONLY in first-user-message preamble.

## MemPalace (Direct Integration)

Imported as Python package — no MCP, no subprocess.

**Vocabulary**: **Drawer** = atomic verbatim chunk (~800 chars, content-hash id). **Closet** = index layer (topic|entities|→drawer_ids) boosting search. **Room** = topic bucket. **Wing** = namespace. **Hall/Tunnel** = graph edges (future).

**Wing scheme** (ID-only): `user__<uid>` (private), `team__<tid>` (team-shared), `project__<pid>` (strictly isolated), bare names (shared, anyone reads). `_resolve_session_wing` priority: project → team → user → empty. Anonymous → `""`, skipped by chat-sync.

`mempalace_query`: when `project` set, **force-scopes** to `project__<id>` (refuses if id missing rather than leak). Else defaults `user__<current_user_id>`. Unspecified-wing visibility filter: drops `project__*`, matches `user__/team__` against caller, treats untyped as shared.

**Chat sync classifier gate**: LLM classifies message pairs before filing. `fact`/`preference`/`decision`/`reference` filed; `generic`/`refusal`/`chitchat` skipped. Non-streaming, `max_tokens:20`, fail-open. Per-session 3-state: `0=off`, `1=on`, `2=auto`. `save_chat_to_memory` tool enables on "remember this". Per-turn control via palace-icon menu → `memorize_turns`/`purge_turns` (`turn_ids` or `{scope, anchor_turn_id}`). Disable-with-purge prompt when toggling on/auto → off w/ drawers present.

**Session delete cleanup**: `delete_session` purges drawers + closets `source_file LIKE session/<sid>%`. **Archive leaves drawers intact.**

**Daemon 1 — `mempalace-miner`** (1800s default): autonomous artifact ingestion. Walks `AGENTS_DIR`, classifies by folder: `sched-` → scheduled, else chat.
- Sched: file only output-role files via `tool_add_drawer`, skip intermediates. `source_file=session/sched-<run>#artifact/<name>`, wing `<agent_id>_artifacts`. Sched chat content (reasoning/tool calls) stays out.
- Chat: gated on parent `save_to_memory > 0`. Ensures `mempalace.yaml` (marker `# managed by brain-agent server.py`).
- Startup `_purge_orphan_chroma_queue()`: detects HNSW segments missing `max_seq_id`, deletes >24h `embeddings_queue` rows.
- plist `PYTHONUNBUFFERED=1` so `[mempalace-miner]` reaches log immediately.

**Daemon 2 — `mempalace-chat-sync`** (60s default): mirrors to wings:
- Chat turns → `room=chat`, `source_file=session/<sid>#turn/<user_msg_id>` (anchor = opening user msg DB id)
- Summaries → `room=chat_summary`, content-hashed
- Attachment metadata (filename/mime/size, NOT bytes) → `room=chat_attachment`
- Allowlisted tool_results (`exa_search`/`web_fetch`/`read_document`) → `room=reference`
- Uses `mempalace.mcp_server.tool_add_drawer` (fn). Reads `MEMPALACE_PALACE_PATH` from `mempalace.palace_path` before import.
- **Closet rebuild** per dirty group: `purge_file_closets` + `build_closet_lines` + `upsert_closet_lines` (else chat memories miss closet boost). Gated by `mempalace.chat_sync.build_closets`.
- **Cursor**: `chat_mempalace_sync (session_id PK, last_message_id, last_summary_hash, updated_at)` in chats.db.
- **Not mined**: attachment bytes, artifact version history, tool_result outside allowlist.
