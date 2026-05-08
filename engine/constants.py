# Extracted from claude_cli.py — constants, tool definitions, model profiles
#!/usr/bin/env python3
"""Brain Agent — Agentic CLI for interacting with LLM APIs."""

VERSION = "8.27.0"
VERSION_DATE = "2026-05-08"
CHANGELOG = [
    ("8.27.0", "2026-05-08", "Image generation via Mistral Conversations API. New `generate_image` tool in a new `image_gen` tool group (opt-in, not in DEFAULT_TOOL_GROUPS). Tool accepts prompt, aspect_ratio (1:1/16:9/9:16/4:3/3:4), and optional style hint. On first call, lazily creates a Mistral-hosted image agent (mistral-medium-latest + image_generation tool) via POST /v1/agents and persists the agent_id to config.json. Subsequent calls reuse the cached ID (in-memory + on-disk). Image generation POSTs to /v1/conversations with the prompt, extracts tool_file chunks from the response, downloads each PNG via /v1/files/{id}/content, saves to the session artifact folder, and triggers _after_file_write for automatic artifact panel registration. Mistral API key resolved from the existing Mistral provider entry in config.json (any provider with api.mistral.ai base_url). Implementation in engine/tools/image_gen.py follows the same cross-module globals pattern as other tool modules (CONFIG_PATH, _ok, _err, _thread_local, _current_agent, AGENTS_DIR, _after_file_write from brain.py). Enable per agent by adding 'image_gen' to tool_groups in agent.json."),
    ("8.24.4", "2026-05-04", "Per-turn `[Files in this chat — ...]` block injected on every user message at round 0 of every turn (not gated by _has_assistant). Reads `artifacts` rows for the session_id and renders each as `path (role, size)`. Tells the model to call `read_document` for content rather than guess from filenames. Caps at 50 most-recent artifacts with a `(+N older)` tail. The block is stripped + re-injected fresh each turn (idempotent on retries; survives compaction). Symptom this fixes: long chats where a tool wrote a large file, the conversation grew, the original tool result got compacted out, and the model lost the path — follow-up questions about file content failed because the model didn't know what was on disk. Now every turn carries the full file inventory. Sentinel `_files_block_injected` stripped via existing _ALLOWED_MSG_KEYS whitelist."),
    ("8.24.3", "2026-05-04", "Workflow refs/artifacts go through the regular chat panels — banner stops duplicating UI. The wf-detail-files card inside the banner is gone; references appear in the right-panel References tab and outputs appear in the Artifacts tab, same as any other chat. **Server seeds at session-create time**: `_handle_workflow_get_or_create_session` runs `_seed_artifacts_for_run` on `created=true` — registers each output path as an `artifacts` row under the bound session_id (with a content snapshot ≤5MB so the viewer renders it; bigger files leave content NULL and the existing disk-fallback path serves bytes from `artifact.path`), and returns the input paths in the response so the client populates `state.chatReferences[sid]` BEFORE openSession runs. The promote handler now factors through the same helper (idempotent — repeats on a session created pre-v8.24.3 catch up; normally a no-op). Removed: `wfDetailRenderFilesHtml` + `wfDetailExtractFiles` + `_wfFileIcon` + `wfFilePreview` + `wfFileDownload` + the file preview modal + matching CSS — single source of truth for path classification now lives server-side in `_workflow_run_paths_classified`."),
    ("8.24.2", "2026-05-04", "Workflow run open: route through the regular chat view. The standalone wf-detail surface is gone — clicking a history row (or hitting Run) now opens the bound chat session in the regular chat view, with a workflow-run-banner above #messages-container that carries the run-specific UI (header, files, collapsed trace, actions). The composer is the regular chat composer, so file-attach / thinking levels / model selection / refs / artifacts all work identically to a normal chat. Header now shows agent + model + started/finished timestamps + duration + cost + workflow_name. Run trace is collapsed by default; expandable to show workflow source + tool-call/result pairs + return value. Save-to-chats stays in the banner; promoting flips status active and the banner switches to a 'Saved' affordance — chat is forever recognizable as workflow-derived. New endpoint POST /v1/workflows/history/<exec>/session looks up (or creates) the caller's bound chat session for this run; reopening the same run hands back the same session so conversations survive across visits. workflow_run_id flows through GET /v1/sessions/<id>/messages so openSession() restores it onto the chat object. sendMessage gates while the run is live (composer refuses with a toast). Removed: wf-detail container + ~300 lines of duplicated turn-rendering / follow-up-list / detail composer JS + matching CSS."),
    ("8.24.1", "2026-05-04", "Workflow detail view: references + artifacts panel, click-to-expand long bubbles, transcript download, save-to-chats carries files forward. (1) **Files panel** above the chat-style turns: heuristic scan of steps_json (path/file/audio/etc. tool args + bare quoted absolute paths) classifies each touched file as input/output/other and renders a collapsible card with View + Download buttons per row. Backend `_workflow_run_paths_classified` mirrors the same regex so the gate matches what the UI shows. (2) **New scoped endpoints** `GET /v1/workflows/history/<exec_id>/file?path=…` (download) and `/file-preview?path=…&lines=N` (text/image/audio/document preview JSON). Path gate: file must demonstrably appear in the run's steps_json or return_value, AND the user must own the run (or be admin). Sidesteps the general file validator's `/tmp` blocklist without opening it. Reused inline-disposition logic for images/audio/PDFs so View opens in a new tab; binary types fall through to download. (3) **Click-to-expand bubbles** — tool-call results, workflow source pre, and the final return-value pre now render collapsed when content >800 chars, with a Show-more/Show-less toggle. Expanded view caps at 60vh + overflow-scroll inside the bubble so a 50KB tool result doesn't blow up the whole view. (4) **Download transcript** button next to Save-to-chats emits a single Markdown file containing workflow source, every step (full detail, no truncation), return value, error, and the follow-up conversation. (5) **Save-to-chats now carries files forward**: promote-session also runs the classifier and registers `artifacts` rows for output paths under the promoted session_id (so the regular Artifacts panel auto-populates) + returns the input paths in the response so the client seeds `state.chatReferences[sid]` (so the References panel shows the workflow's read files immediately). End result: a saved workflow chat behaves like a regular chat — references + artifacts panels work the same way."),
    ("8.24.0", "2026-05-04", "Workflow run history: inline chat-style detail view replaces the run modal. Clicking a history row (or hitting Run) now takes over the workflow tab area with a full-height detail view that renders the run as chat-style turns: workflow source as a system bubble, each tool call paired with its result as a tool turn, final return value or error as a final turn. Composer below is gated on terminal status — disabled while the run is `running`/`pending`/`waiting_approval`, enabled once `completed`/`failed`/`cancelled`. First follow-up message lazy-creates a hidden chat session bound to the workflow_run_id (status='workflow_run', hidden from sidebar via list_sessions filter); subsequent messages reuse it for full conversation context. **`Save to chats` button** appears once a follow-up exists; flips status to 'active' and navigates the user to the freshly-promoted session in the regular chat view. **Per-run preamble** — `_workflow_run_preamble_text()` builds a compact summary (workflow source + chronological tool-call trace + return value/error) and injects it into the first user message of any chat session bound to the run, so the model can answer `why did the second tool call fail?` without the user re-pasting context. Mirrors the existing `note_context` mechanism so KV-cache stability is preserved (system prompt stays workflow-agnostic; per-run bytes only live in the user message). **Schema**: new `workflow_run_id TEXT` column on `sessions`, indexed; `Session.workflow_run_id` field; `ChatDB.update_session_workflow_run_id` + `update_session_status` helpers. **New endpoint** `POST /v1/workflows/history/{exec_id}/promote-session/{sid}` flips the hidden session to active (refuses cross-binding mismatch + non-owner). **Removed**: the old `wf-run-modal` markup + `wfOpenRunModal`/`wfCloseRun`/`wfCancelRun`/`wfRenderRunState` are deleted; `wfShowHistoryDetail` and `wfRun` both delegate to the new `wfOpenDetail()`. Active runs poll the live endpoint as before; on terminal transition the detail view re-fetches the persisted /history row so the chat-style view shows the full final trace + return."),

    ("8.23.4", "2026-05-03", "KG extraction error surfacing in sync history. Parse errors (malformed JSON returned by the LLM for individual chunks) were previously hidden in two ways: (1) the sync history modal showed only the raw error string ('no JSON array in response') and hid the triple count entirely, making a partial failure look like a complete KG failure; (2) the per-folder pill in the project panel showed 'KG !' with no triple count when `kg_state='error'`, and showed nothing for parse errors when the state was still 'idle' (triples were extracted but some chunks failed). Fixed across three layers: **server.py** now stores `kg_parse_errors=int(res.errors)` in the item state and passes it through both `step_update` calls (ingested attachments + input folders) so the count is persisted in the sync run log. **Sync history modal** (`panels.js` `_syncRunDetailHtml`): KG row always renders the stats line (triples this cycle, total, drawers processed) first; parse errors are appended as an amber warning span with the error message in the tooltip; only a hard failure with zero triples falls back to the red `⚠` label. **Per-folder pill** (`projectItemPillHtml`): always shows triple count; when `kg_parse_errors > 0` the pill switches to `data-kg='warn'` (new amber CSS class in `main.css`) with the error count appended (`N relations · M parse err`); tooltip explains these are non-fatal. The `data-kg='error'` path also now includes the triple count before the `KG !` label so a partial failure is unambiguous. `kg_parse_errors` in the log is zero for all-success runs so the warning only appears when there were actual parse failures."),
    ("8.23.3", "2026-05-03", "Sync history overhaul — detailed phase logging, three bug fixes, project chip sub-label, table-layout modal. (1) **Detailed sync run logging** — `engine/sync_log.py` grows `log_purge_actions(run_id, actions)` that appends structured action records (`action`, `elapsed_s`, counts) to `log.purge_actions[]` in the run row. `_handle_project_full_resync` in `handlers/projects.py` creates a dedicated `full_resync_purge` sync run before touching anything, records all four purge steps with counts + elapsed: `drawers_purged` (deleted count from `purge_by_prefix`), `kg_triples_purged` (triples_deleted, progress_cursors_deleted, prefixes_count), `closet_cursor_cleared`, `doc_convert_cache_cleared` (dirs_removed, files_removed). Per-folder sync steps also gain `elapsed_s` fields for doc_convert, indexing, KG, and closet-rerank phases so every section of the expanded run detail shows timing. (2) **Bug fix: `purge_by_prefix` deleted 0 drawers** — `MemPalaceClient.purge_by_prefix` was calling `get_collection(palace_path, wing=wing)` but MemPalace's `get_collection` signature takes `(palace_path, collection_name, create)` — the `wing` kwarg was silently ignored, returning the global unfiltered collection. Fixed: now queries `col.get(where={\"wing\": wing})` to get only the wing's documents, then filters by `source_file.startswith(prefix)` before deleting. Closet collection is purged by the same filter (was missing entirely before). Full resyncs were doing 0 drawer deletions; they now correctly delete all wing drawers. (3) **Bug fix: KG extraction processed orphan drawers after source files deleted** — `_process_source` in `engine/kg_extract.py` would reprocess drawers whose `source_file` no longer existed on disk. When `cur_mt == 0` (file not found), the fix checks `os.path.isfile(sf)` and calls `_invalidate_source_in_kg` to purge stale triples + progress cursor before skipping. Same guard added for per-drawer chunking mode. Without this, full resyncs that wiped drawers and re-ran KG extraction were re-generating triples from orphan drawer content. (4) **Bug fix: closet rerank treated missing-on-disk files as 'unchanged'** — `_iter_wing_source_files` returning `mt=0, sz=0` for files not on disk were put in `skipped_no_disk` and excluded from `stale_sources`, so the closet regen short-circuited with '0 sources stale'. Fixed: missing-on-disk files now append to `stale_sources` (force regen) instead of being silently skipped. (5) **`last_triggered_by` in sync_status** — the `sync_status` JSON persisted to `project.json` now includes `last_triggered_by` (`scheduled` / `manual` / `full_resync`) from each completed sync cycle, so the UI chip can show the correct sync type without a second fetch. (6) **Sync history modal rewrite** — completely replaces the old flat key-value layout. New `_syncRunDetailHtml` renders all per-folder phases (doc-convert, indexing, KG extraction) and the project-wide closet-rerank step in **one shared `<table>` with fixed column widths** (`110px` label, flex detail, `44px` elapsed right-aligned) so all rows across all folder sections align. Folder section headers use a `colspan=3` row instead of a separate `<div>` so the table is never broken by intervening elements. Full Resync entries render a Purge section (4 action rows, `160px` label column) + Re-index section separated by the section header; purge elapsed also appears in a right-aligned third column. Lazy detail loading: `_loadRunDetail` fetches the full run log on first expand (list endpoint returns only summary); parallel-fetches both purge + resync runs for paired entries. (7) **Memory chip sub-label** — project chip grows a second text line (`project-sync-sublabel`) below the main label, rendered as `synced Xh ago · Scheduled` (or `Manual` / `Full Resync`) using `last_triggered_by` from sync_status. Only appears when a sync has run; cleared during active syncing / error / KG extraction states. CSS: `project-sync-text` is a `flex-direction:column` wrapper; dot gets `align-self:flex-start; margin-top:3px` so it top-aligns with the first line instead of centering on the two-line block."),
    ("8.23.0", "2026-04-30", "Token-saving optimisations + project Instructions refactor + Instructions UI polish. Three coordinated changes targeting per-turn token consumption + owner-editable response disciplines + better readability of the Instructions panel. (1) **A1+ per-session read_document / read_file cache** — `_read_doc_cache` in claude_cli.py keyed by `(session_id, abs_path)` with `(mtime, size, turn_first_read, content_hash)` value. On a repeat full-shape read of the same file in the same chat, returns a compact stub `{cached:true, first_read_in_turn:N, content_hash:..., note:'Bereits in Turn N gelesen — Inhalt unverändert'}` instead of streaming the file content back into the model's context. mtime+size check on every lookup invalidates if the file changed externally; `_after_file_write` invalidates the entry explicitly when the model writes/edits the same path so a follow-up read sees fresh content. Pagination args (offset / limit / pages / sheet / slides; explicit `limit` on read_file) bypass the cache entirely. read_document and read_file are added to `_DEDUP_EXEMPT` so the bare-string dedup (which kills the loop after 2 dupes) doesn't fire on legitimate cache-hit calls — the cache is the smarter dedup. `_thread_local.tool_round` set in `_execute_tools_batch` so the stub names the right turn. TTL 1h, max 64 entries per session. Validated on a real workflow: yesterday's 4-turn DSGVO chat (78beef49) used 335,683 input tokens reading the same Datenschutzhandbuch.pdf.md three times across turns; today's reproduction (1e7478d9) used 277,579 tokens — a measured −17% reduction DESPITE the model choosing to read 3× more source files per turn (3 .md companions instead of 1). User estimate normalised to a 1-source workflow comes out around −30%; without A1+ the 3-source variation alone would have inflated tokens far above yesterday's number. Per-round growth on Turn 2 dropped from yesterday's +2,605 (round 0 → 1) to today's +655 with three read_documents in flight, all hitting cache. (2) **B1 project preamble** — moved dynamic project state (drawer count, attachment count, input-folder list with absolute paths, path-join example) out of `_build_system_prompt` into a per-session preamble injected at round 0 on the first user message. New helper `_project_preamble_text(agent_id, project_name)` builds the `[Project context (this session): …]` block. Injection block hoisted out of the `_greeting_uid` guard so anonymous (auth-off) sessions in projects also get the absolute paths needed to resolve relative drawer source_files. KV-cache stability win: system prompt stays project-agnostic in shape, the per-project bytes only live in the user message — better prefix reuse on warm-pool slots and across project chats; saves ~1KB per request on Cloud providers without prompt cache. (3) **DEFAULT_PROJECT_INSTRUCTIONS constant + project Instructions refactor** — REFUSAL + PRECISION + CITATION discipline blocks (the v8.22.0 anti-hallucination rules) moved out of `_build_system_prompt`'s static text into a new module-level `DEFAULT_PROJECT_INSTRUCTIONS` constant (4081 chars). Used as the FALLBACK when `project.json.instructions` is empty so every project still gets the disciplines for free with no migration. Project owners can replace the default by writing their own text in the right-pane Instructions editor; override REPLACES the default rather than appending (an owner can opt out of the citation requirement by writing simpler instructions). New endpoint `GET /v1/projects/default-instructions` returns the constant; the editor modal grows a `Load default` button that pre-fills the textarea so owners can start from the disciplines and customise. Brain mechanics (the 3-step retrieval flow, `read_path` vs `read_path_original`, binary→.md companion explanation, KG hint block) STAY in the system prompt because those are infrastructure facts not editable behavior. (4) **Instructions UI polish** — the right-panel Instructions section now renders the saved instructions text as full markdown (headings / bullets / code / blockquotes / strong) via the existing renderMarkdown(). Section is height-capped at 200px with vertical scroll so long defaults (4KB markdown) don't push Files + Input-Folders sections below the fold. Editor modal grows Edit/Preview tabs above the textarea: Edit shows raw markdown (existing textarea, min-height bumped to 320px), Preview renders the current textarea content via the same renderer with up to 480px scrollable height. Switching tabs preserves the raw text; Save always reads from the textarea regardless of which tab is active. Load default refreshes the Preview if it's currently visible. Save path now also re-renders the right-panel block as markdown to match loadProjectDetail (was raw textContent before). (5) **Token-optimization memory captured** — three new memory notes documenting A1+/B1 mechanics, the validated −17% measurement, and a backlog item for lifting REFUSAL/PRECISION/CITATION into an org-wide setting (the user explicitly said 'lass mal' on that — don't apply prematurely)."),
    ("8.22.2", "2026-04-29", "References UI split into Zitiert + Durchsucht. The right-side References panel and the inline ref-badges under each assistant message previously rendered EVERY drawer that came back from `mempalace_query` — typically 5-10 results, of which the model only quotes 1-3 in `[Quelle: …]` markers. Visual noise made it hard to tell which sources were actually used. New split: refs whose basename matches a `[Quelle: <basename>]` marker in the assistant message text land in the Zitiert section (always visible); the rest land in Durchsucht (collapsed by default via `<details>`). When an answer cites no sources (refusals, no-source answers), Durchsucht opens by default so the pane isn't empty. Implementation: new `extractCitedBasenamesFromText` regex parses both `[Quelle:…]` and `[source:…]` markers (em-dash, en-dash, ASCII-hyphen-with-spaces, `§`, or closing-bracket all valid terminators); `normaliseCitationBasename` lowercases + strips path prefix + strips `.md` companion suffix on known binary extensions (pdf|docx|pptx|xlsx|xlsm|eml|msg) so `policy.pdf`, `policy.pdf.md`, and `Policy.PDF` collapse to the same key. `collectChatReferences()` and `getReferencesForMessage(idx)` return `{cited, searched}` instead of a flat array. Live-streaming refs always seed the searched bucket; cache is invalidated on `done` event so the next read re-splits using the now-final assistant text. Two new render paths share a single `_refCardHtml` helper; CSS for `msg-references-wrap` (per-message inline) + `refs-section` (right pane) wrap each section with a header label + count pill and `<details>`-driven disclosure on the collapsed surface."),
    ("8.22.1", "2026-04-29", "CITATION DISCIPLINE tightened: per-claim instead of per-block. Follow-up to v8.22.0 after the canary `a82327b7` showed the model still ending bullet lists with three uncited paraphrase bullets after a single inline blockquote — exactly where drift and fabrication slip in (the user can't tell which bullet came from which source). Reworked the CITATION DISCIPLINE block to mandate that EVERY sentence AND EVERY bullet carry its OWN `[Quelle: <basename> — \"<verbatim quote>\"]` reference, not just one at the end. Added explicit guidance: if no verbatim quote can be found for a bullet, DELETE the bullet — shorter fully-cited answer beats longer answer with uncited bullets. Two worked examples (single-sentence claim + bullet list with per-bullet quotes plus an explicit '(kein dritter Bullet, weil keine weitere Aussage im read_document gefunden — lieber weglassen als raten)' line so the model sees the deletion pattern modelled, not just told)."),
    ("8.22.0", "2026-04-29", "Anti-hallucination retrieval stack + sampling + citation discipline. End-to-end stabilisation pass on the German bank-policy corpus after a day of validation runs that exposed a chain of three independent bugs masquerading as 'the model hallucinates'. (1) **PRECISION DISCIPLINE block** added between REFUSAL and CITATION DISCIPLINE in `_build_system_prompt`'s PROJECT MEMORY scope: bans plausible-sounding filler ('regelmäßig', 'häufig', 'sofort', 'kürzer', 'mindestens X Zeichen', 'alle 12 Monate', 'mindestens jährlich'), requires `nicht spezifiziert` when the source gives no concrete value, and gates every qualifying adverb/comparative on an immediately-following wörtliches Zitat from the read_document output. ISO-27001-typical phrasing from training data is explicitly NOT a source. (2) **CITATION DISCIPLINE rewritten** to mandate verbatim 10-25-word quotes inside the bracket — `[Quelle: <basename> — \"<wörtliches Zitat>\"]` — instead of the old `§N` style. The `.md` companions don't preserve the original document's paragraph numbering, so any `§N` the model wrote was fabricated; switching to verbatim quotes makes citations self-verifiable (user can Cmd+F the original PDF). Locator additions (`Page N` for PDF, `Slide N` for PPTX, `Sheet \"Name\"` for XLSX) are allowed only when genuinely visible in the read_document text. Worked example uses a real Multilogin sentence so the model has a concrete pattern to copy. (3) **Validated sampling defaults** for Mistral Small 3 on policy-reproduction: `temperature: 0.2` + `top_p: 0.85`. Captured operationally — across six measured runs on the same canary the combination dropped fabricated formulae, fabricated paragraph numbers, fabricated intervals, and fabricated thresholds while preserving correct retrieval and citations. `temperature: 0` was tested and rejected (Mistral provider rejected the request) and `temperature: 0.1` showed no measurable improvement over 0.2. The combination is now the user-set default in config.json (gitignored); CLAUDE.md notes the validation. (4) **Anti-room-name-guessing** — the `mempalace_query` tool's `room` parameter description was a permissive list of speculative example rooms ('document', 'documentation', etc.) which the model used as valid vocabulary, returning zero drawers and producing false 'not in the documents' answers. Description rewritten to enumerate the actual rooms Brain's miner uses (`general` for policy/document content, `artifacts`, `chat`/`chat_summary`/`chat_attachment`, `reference`) and explicitly forbid guessing. Validated on the canary against `9775bba7` → `a82327b7`: same model, same query, same retrieval, output dropped from 3850 chars (multiple fabricated sections incl. `§154–176`, 'alle 12 Monate', 'mindestens 20 Zeichen') to 1037 chars with a single inline blockquote, a single citation carrying the verbatim source text, and three paraphrase bullets — every claim backed by the read_document output. The day's other infrastructure fixes (markitdown wrapper preferred over fitz/python-docx for materially better markdown, read_document plain-text pagination respecting `offset`/`limit` instead of hard-capping at 500 lines, drawer.read_path / read_path_original carried through tool_mempalace_query, doc_convert frontmatter `brain-converter` stamp for backend traceability, KG disabled in default config because vanilla MemPalace+Claude Code outperformed Brain's KG-augmented stack on the IT-risk-score canary, _summarise_tool_result 3-tuple unpack fix in maybe_retroactive_isolate that was crashing every >65KB read_document call and being misread as hallucination, project__-wing startup wipe scoped to knowledge wing only — chat content preserved) ship with this version, plus matching memory notes (project_chroma_direct_search_fix, project_read_document_truncation_fix, project_drawer_path_resolution_fix, project_kg_disabled_markitdown_swap, bug_summarise_tool_result_unpack, project_brain_canary_55_of_7, project_vanilla_mcp_gap_analysis, project_drilldown_tools_added)."),
    ("8.21.6", "2026-04-28", "Project actions buttons + per-response Memory & Relations graph. Six coordinated UI changes. (1) **Project header gets explicit action buttons** — `Sync now` and `Knowledge graph` (admin-only). The chip itself is now informational (cursor:default, no onclick); double-click to open KG was unreliable (one user-reported bug had it stop firing) so the surface was demoted. Sync button disables while `state==='syncing'`; KG button disables when there are zero relations and is hidden entirely for non-admin roles since the modal is debug/audit territory (predicate distribution, sample triples, extraction log, admin re-extract — useful for verifying corpus quality and audit prep, not end-user reading). (2) **KG modal was visually transparent** — `kgOpenProject` and `_kgShowInfo` rendered the inner card with `class=\"modal\"` but the styled rule is `.modal-content`; class typo meant no background, no shadow, no rounding so the modal looked like floating text on the page underneath. Fixed both. (3) **Inline 'Show used Memory and Relationships' button per assistant response** — appears only on assistant turns that actually called `mempalace_query` / `mempalace_kg_*`, gated by new `messageUsedKnowledge(idx)` helper that reads metadata.tools[] post-reload + tool_result rows live. (4) **Memory & Relations graph modal** — opens from the inline button; renders a 3-column SVG (Documents → Subjects → Objects, left-to-right) with rounded pill nodes that fit long German entity names instead of truncating to unrecognisable stubs. Predicate labels ride along cubic-Bezier edges via SVG textPath so they never overlap nodes. Height auto-sizes to dataset (no more empty top half on small retrievals). Empty columns hidden entirely (a docless KG-only retrieval renders 2 columns instead of forcing a tiny 3rd). Side panel lists every drawer with similarity score + snippet, plus every relation with source file + confidence so the user can audit which document each fact came from. (5) **Source-link dotted edges**: when a triple's `source_file` matches a doc node by basename (case-insensitive, .md companion stripped, full-path vs bare-name normalised) a dashed edge connects them — visually answers 'which document produced this fact?' even when MemPalace returned a triple without fetching that doc's drawer. Doc nodes auto-created from triples too, not just drawers, so the graph shows every source the answer used. (6) **Plain-English tagline** above the graph explains what drawers/relations are and why the user might want to look — replaces having to learn the vocabulary from the docs."),
    ("8.21.5", "2026-04-28", "Status-bar hidden on chatless views + scheduled-run inspector reroute. Two coordinated UI fixes after the v8.21.4 ship. (1) **Status bar leaked the previous chat's data on every view that has no active session in scope**: the per-chat status bar (session id, model, tokens, cost, context fill, warmup dot) was rendered with `display: ''` on welcome / projects / project-detail / scheduled / artifacts. `updateStatusBar()` reads `state.activeChat`, so when the user navigated away from a chat the bar kept showing that prior chat's numbers — read by users as 'current state' which it isn't. Now hidden on every view that doesn't have a chat in scope: welcome, projects, project-detail, scheduled, artifacts. Bar still shows on `chat` and `chats`. project-detail also drops the `updateStatusBar()` call from its post-navigate refresh block since the bar is hidden. (2) **Status-bar inspector button on scheduled-run chat view routed to the generic Session Inspector** instead of the scheduled-run details modal — but scheduled runs already have a much richer per-run modal (`_schedViewRunDetail`: timeline + tool spans + artifacts + result text) that the History table's 'Details' button uses, plus the run banner's 'Details' button. The generic inspector is per-turn / per-round LLM-call detail, geared toward debugging a live chat — not what the user wants when looking back at a completed scheduled run. `openInspectModal()` now detects `sessionId` matching `^sched-(\\d+)$` and routes to `_schedViewRunDetail(runId)` instead. Non-scheduled (`uuid` session id) chats fall through to the existing per-turn inspector unchanged. Single source of truth for 'what happened on this run' regardless of whether the user enters from the history table, the run banner, or the status-bar magnifying glass."),
    ("8.21.4", "2026-04-28", "Project right-pane UX overhaul. Six coordinated changes addressing user feedback on the project detail view. (1) **Memory chip rebuilt around what users actually count**: idle label now reads `Memory: N files · M relations · next sync in Xh` instead of `N indexed`. `total_files` is a new server-tracked distinct-source-file count (was only drawer count before, which conflates 800-char chunks with files); `next_run_at` is derived from `last_run_finished + interval_seconds` and exposed in the `/sync-status` response so the chip computes its countdown without a second config fetch. **Live progress during sync**: chip flips to `Memory: syncing P/T files · ETA Xm (current folder)` — daemon does a cheap pre-walk at cycle start to compute `cycle_total_files`, then bumps `cycle_processed_files` after each attachment batch / folder finishes (incl. error-skip paths so the bar never stalls below total). ETA is elapsed-extrapolated from wall time and progress share, gated to ≥5% completion so an early 0/N doesn't claim '12 days remaining'. Tooltip carries the technical drawer count + last-synced timestamp. (2) **'triples' → 'relations'** in every user-facing surface: chip label, per-folder pill, KG sub-badges. The KG concept is too jargony for end users — 'relations' reads as 'facts we extracted' in plain English. Settings → Knowledge Graph admin tab kept the technical term since that audience needs it. (3) **Input-folder rows redesigned**: was a single overflow-prone flex row with overlapping badges on narrow panes; now a 3-line block (folder name bold + edit/delete actions on top; full absolute path in mono with RTL ellipsis to keep the tail visible; badges wrap-flex on a third line). Added auto-sync state badge, made delete/edit affordances explicit SVG buttons. (4) **Edit-folder modal**: pencil button on each row opens a modal with the same picker shell as Add, prefilled with the current path/recursive/auto_sync. Backed by new `POST /v1/agents/{id}/projects/{name}/input-folders/{idx}` for partial update (path / recursive / auto_sync); path-change goes through the same validation as add so dedup against other entries is enforced. PATCH wasn't dispatched by the HTTP server, so the update lives on POST under the indexed sub-path. (5) **Auto-sync gate per folder**: new `auto_sync` field (default true) on input_folders entries. Daemon honors `auto_sync=false` on scheduled cycles — these folders show a `paused` row state and contribute 0 to `cycle_total_files`. Manual 'Sync now' overrides the gate so the user can still trigger an on-demand pass. Add modal grew an 'Include in automatic sync cycles' checkbox (defaults to checked); same checkbox in the edit modal. (6) **Delete-confirm modal** replaces the bare `confirm()`: amber warning icon + path readout + red 'Remove folder' button. Auth still gates on `_project_access_check(require_manage=True)` for the underlying DELETE. (7) **Draggable right pane**: added a `col-resize` handle on the left edge of `.project-detail-panel`, mirrors the `#right-panel` pattern. Width persisted to `localStorage('project-detail-panel-width')`, clamped 240-640px. (8) **Project composer placeholder + instructions branding**: composer textarea placeholder now reads `Write your message to <ProjectName>` (set dynamically in `loadProjectDetail` from `project.name`, falling back to slug); the static `placeholder=\"Write your message to Claude\"` was misleading on a Brain Agent install. Instructions empty-state placeholder updated from 'customize Claude\\'s responses' to 'customize Brain Agent\\'s responses' in all three rendering paths."),
    ("8.21.3", "2026-04-28", "Hydration substitution applies outside project context too. The v8.21.2 substitute path was gated on `current_project`, so user-wing / team-wing / shared `brain_code` searches still got duplicate-frontmatter hits when their content had a repeating-title document. The gate was a refactor leftover — none of the substitute logic actually requires a project, only the input-folder probe does. Now: dedup runs unconditionally; substitute runs whenever palace_path is set, with the input-folder probe applied only if we happen to be project-pinned. Outside projects we skip straight to the last-resort wildcard metadata scan (one Chroma read; only fires for hits where the searcher's text didn't already include the user's rare query terms). Chat-attachment scenarios specifically: non-project chat attachments are NOT mined into MemPalace today (only filename+mime+size go in via the `attachment_metadata_drawer` path); the agent reads them through `read_document` against /tmp/brain-attachments/<sid>/. So this fix doesn't change anything for raw uploaded content — that gap is a separate design question. It DOES help past-chat-memory hits in the user wing when chat-sync filed a long Q+A turn whose first chunk is mostly the question header."),
    ("8.21.2", "2026-04-28", "MemPalace search hydration substitution — fixes 'agent says topic isn't in the doc when it actually is.' Symptom on real bank-policy corpus: query 'SPAM Malwareschutz' returned 7 high-similarity hits all pointing at `ANW_20_2_9_Malwareschutz.pdf.md` (sim 0.97 / 0.89 / 0.76 / ...) — but every hit's text was the IDENTICAL 1452 chars of the doc's frontmatter + title page. The agent saw 'no spam content here' and reported the policy doesn't cover the topic, while in reality chunk 6 of the doc has 'Anti-Spam Filter zur Blockade verdächtiger Emails ...'. Root cause is in MemPalace's searcher.py (drawer-grep enrichment, line 472-491): for every closet-boosted hit on a multi-chunk source, it re-runs the SAME logic — pull all chunks for the source, score by `set(query_tokens) & set(chunk_tokens)` membership (dedup count, NOT frequency), pick the chunk with the highest count and return [best-1, best, best+1] joined. Because the doc title `Malwareschutz` appears in EVERY chunk's heading + brain-source frontmatter, every chunk scores 1 on the set scorer; ties resolve by iteration order so chunk 0 wins for every hit. Fix lives in Brain (not upstream — touching the venv'd MemPalace package gets clobbered on reinstall): `tool_mempalace_query` now (a) dedupes by source_file (keeping highest-similarity hit per source — 7 identical Malwareschutz hits → 1), (b) when the kept hit's text doesn't mention the 'rare' query tokens (those NOT present in the source filename — for our query, `spam` is rare since the filename has `Malwareschutz` but not `Spam`), scans the source's other chunks via Chroma `where={source_file=...}` and substitutes the chunk with the highest **count** (not set-membership) of rare-term hits. matched_via stamped `drawer+keyword-substitute` so it's traceable. Probes likely paths first (project input_folders + their `.brain-extracted/` companions) before falling back to a metadata scan, so hot-path cost is one Chroma `get` per substituted hit. Without rare-term substitution the bug is structural for every German policy doc whose title repeats in chunk frontmatter — Steuer-/HR-/IT-policies all hit it equally."),
    ("8.21.1", "2026-04-28", "Reference badges survived only for the first cited document. Two coordinated bugs cropped up after v8.21.0 in a multi-source answer (one drawer cited a Malwareschutz policy, second drawer cited an Internet/E-Mail policy — only the second showed in the inline badges, and after reload both vanished from the right panel). (1) **Server cap**: `_handle_chat`'s tool_result handler in server.py truncates the persisted `metadata.tools[i].result` to 500 chars for every tool except `exa_search`/`web_fetch`. `mempalace_query` and the three KG tools also surface clickable references but were on the 500-char path — enough for the first drawer's metadata + ~200 chars of text, then JSON cuts mid-string. After reload the truncated string fails JSON.parse entirely, the regex fallback finds only the first complete `\"source_file\": \"...\"` token, second reference disappears. Added the four project-knowledge tools to the higher-cap list and bumped to 4000 chars (~5 drawers fit comfortably). (2) **UI regex top-up**: `extractReferencesFromToolResult`'s regex fallback only fired when JSON.parse returned zero refs, so a partial-but-valid JSON object (first drawer parses, rest cut off) produced one ref and skipped the rest. Reworked so the regex sweep always runs and dedupes against the JSON-parsed set — covers any future cap edge case without depending on the JSON being whole. Bare-numeric and `<sid>#summary` skips from v8.21.0 preserved."),
    ("8.21.0", "2026-04-28", "Project chats split off into their own MemPalace wing — `project__<id>` is now strictly mined documents + ingested attachments; chat turns/summaries/attachment metadata go to a new `project_chat__<id>` wing. Two coordinated bugs fixed. (1) **Chat content was poisoning project knowledge retrieval.** When `save_to_memory` was on or auto, every project chat turn (and its session summary) landed in the same wing as the indexed PDFs. Wrong answers from earlier turns then ranked ABOVE the underlying source on later queries because chat-summary and Q-A pairs share more lexical/semantic surface with the user's question than a single 800-char policy chunk does. Reproduced live: a wrong 'TAMBAS not in project knowledge' answer self-reinforced for the rest of the chat while the actual policy chunk (sim 0.50) sat below the chat-derived 'TAMBAS not found' summary (sim 0.68). Fix: `_resolve_session_wing` now returns `project_chat__<id>` for project sessions; `tool_mempalace_query` while project-pinned reads from `project__<id>` only. New optional `include_chat_history=true` flag on the tool flips the read to the chat wing for explicit 'remember when we said' questions. (2) **`source_file: '3247'` clickable refs that opened nothing.** MemPalace's `searcher.py:416` returns `Path(source_file).name`, which strips `session/<sid>#turn/` off chat drawers down to the bare turn-id. The web UI's `extractReferencesFromToolResult` then treated those as document paths, rendering 'broken' clickable cards. Fix: the JSON-parse path skips drawers with `room ∈ {chat, chat_summary, chat_attachment}`; the regex-fallback path defensively skips bare-numeric source_files and `<sid>#summary` shapes. Visibility filter in `tool_mempalace_query` extended so cross-wing searches also exclude `project_chat__*` (project chat is private to the project). System-prompt PROJECT MEMORY block updated to call out the new boundary and tell the model to use `include_chat_history=true` only when the user is explicitly asking about earlier chat turns. Startup wipe in the project-sync daemon tightened to match `project__` but NOT `project_chat__` (the previous prefix match would've nuked chat content on every restart); same scope applied to the closet wipe. KV-cache invariant preserved (the system-prompt block is per-project; warm-pool slots only seed for agent=main, project='')."),
    ("8.20.6", "2026-04-27", "Project source reference clicks: extract from metadata + resolve bare basenames. Two further fixes after v8.20.5 that unblock the actual user flow. (1) **Web extractor walks `metadata.tools[]`**: `collectChatReferences` and `getReferencesForMessage` previously only looked at live `tool_result` rows, which only exist during streaming. After page reload the tool data lives in the assistant message's `metadata.tools[]` array — so old chats had zero refs in the right panel. Both functions now iterate `metadata.tools[]` too, building synthetic `tool_result`-shaped objects fed to the same `extractReferencesFromToolResult` parser. (2) **Server-side bare-basename resolution**: MemPalace drawers carry `source_file` as a relative path or even just the bare basename (e.g. `20_2_2_0_ARL_IT-Endbenutzerrichtlinie.pdf.md`), not as an absolute path. `_validate_file_path` was happily resolving that against the server's CWD into the cctest tree, then the `os.path.isfile` check failed with a misleading 404. New `_resolve_project_basename` helper does basename lookup under any project input_folders[] the authenticated user can see (capped recursive walk, also strips trailing `.<binext>.md` suffix to find the original binary), and `_handle_file_download` now falls through to it when the validator's first pass returns a non-existent path. Verified end-to-end: bare basename, `.pdf.md` companion, and absolute path inputs all return the same 19-page PDF with `Content-Type: application/pdf` and `Content-Disposition: inline`."),
    ("8.20.5", "2026-04-27", "Project source download: allow project input_folders + render PDFs inline. Two server-side fixes that made `openProjectSource` fail even after v8.20.4 stripped the `.md` suffix correctly. (1) `_validate_file_path` only allowed cctest/, agents/, and cwd — but project sources live wherever the admin pointed input_folders[] (e.g. /private/tmp/kg-real-policies/), so every download 403'd. Validator now also accepts paths under any project's input_folders[] for projects the authenticated user can see (resolved via `engine.ProjectManager.list_projects` with the user's id + team ids). Symlink-resolved per-root prefix match. (2) `_handle_file_download` always sent `Content-Disposition: attachment`, so even if the path were allowed the PDF would force-download with a confusing blob:// filename instead of rendering in a new tab. Switched to `inline` for browser-renderable types (pdf/png/jpg/jpeg/gif/svg/txt/md/html/json/csv); office binaries (docx/xlsx/pptx) stay `attachment` since browsers can't render them. Filename now uses RFC 5987 `filename*=UTF-8''<urlquoted>` so German umlauts and spaces don't break the header. Verified end-to-end: 200 OK, application/pdf, inline disposition, valid PDF bytes."),
    ("8.20.4", "2026-04-27", "Citation discipline: strip `.md` companion suffix. The agent was citing project sources verbatim from drawer `source_file` values, so users saw `[Quelle: policy.pdf.md]` in answers and clicking the inline ref-badge tried to open the `.brain-extracted/policy.pdf.md` companion — which doesn't render as a PDF in the browser, producing a 'cannot open' error. Two coordinated fixes. (1) System prompt CITATION DISCIPLINE block in `_build_system_prompt` (claude_cli.py) gets an explicit STRIP rule: when a drawer's source_file ends in `.brain-extracted/<name>.<ext>.md`, cite the ORIGINAL binary's name (`policy.pdf`), never the `.md` companion. Worked example shows the input → output. (2) Defensive fallback in `resolveOriginal` (web/index.html) inside `extractReferencesFromToolResult`: also strip a trailing `.md` from any path ending in `.<binext>.md` where binext is pdf|docx|pptx|xlsx|xlsm|eml|msg, even when the path is NOT under `.brain-extracted/`. Catches cases where the agent's citation skipped the prefix in plain text and the model's text path picks them up. KV-cache invariant preserved (the project block is per-project; warm-pool slots only seed for agent=main, project='')."),
    ("8.20.3", "2026-04-27", "Session-to-project filter switched from project name → project_id. The legacy `sessions.project` column stored the directory name; renaming a project (or two same-named projects under different agents) silently disconnected chats from their project panel. New idempotent ALTER added `sessions.project_id TEXT DEFAULT ''` (uuid4 hex[:12] from project.json, which ProjectManager.get_project mints on first read), plus `idx_session_project_id`. New `_project_id_for_name(agent_id, project_name)` helper resolves directory-name → id by reading project.json. `ChatDB.save_session` accepts `project_id=` and auto-resolves it from the legacy name when not supplied, so every save normalises both columns. `list_sessions` / `archive_all` / `unarchive_all` / `delete_all` accept an optional `project_id=` kwarg and prefer it over the legacy name match. The HTTP handler `_handle_list_sessions` and the archive_all / unarchive_all / delete_all action handlers resolve the API's `project=<name>` query param into project_id once and pass both. One-shot startup backfill (server.py `main()`) walks every distinct (agent_id, project_name) in the sessions table, resolves to project_id, and updates rows where project_id is empty. Idempotent — only fills empty cells. The legacy `project` column is intentionally kept readable for back-compat surfaces (session info display, summary text), but nothing filters on it any more for new sessions. Fixes the recent test-chat regression where Q1/Q2/Q3 sessions disappeared from the project panel after creation."),
    ("8.20.2", "2026-04-27", "Anti-hallucination + clickable project-source citations. Two findings from the real-corpus chat-test (3 queries on 15 German bank IT-policy PDFs) addressed. (1) **System prompt: refusal-on-empty + KG-tool prompting + citation discipline**. The negative-test query 'Was sagt unsere Richtlinie zur Geldwäscheprävention?' (corpus has no GwG content) made the agent fabricate a complete 7,295-char fake policy citing AMLD4/GwG/etc. — both `mempalace_query` AND `mempalace_kg_query` returned empty, and the agent treated 'no results' as 'try harder' instead of 'refuse cleanly'. New project-memory block in `_build_system_prompt`: (a) explicit per-tool guidance for when to use `mempalace_query` vs `mempalace_kg_search` vs `mempalace_kg_query` with German/English examples per predicate (`cites`/`responsible_party`/`requires`/`forbids`); (b) hard refusal rule — when both query types return zero, answer with 'Diese Information ist im aktuellen Projektwissen nicht enthalten — bitte fügen Sie das relevante Dokument zum Projekt hinzu oder konsultieren Sie eine andere Quelle' and STOP, never substitute general knowledge for indexed-document knowledge in project chats; (c) try at most 2-3 query rephrasings before refusing; (d) citation discipline — every project-derived claim must end with `[Quelle: <basename> §<section>]` or `[source: <basename>]` so the UI can wire clickable refs. (2) **Clickable project-source references in right panel + inline badges**. `extractReferencesFromToolResult` (web/index.html) now also extracts file-path refs from `mempalace_query`, `mempalace_kg_query`, `mempalace_kg_search`, and `mempalace_kg_neighbors` tool results. Resolves `.brain-extracted/<name>.<ext>.md` back to the original `<name>.<ext>` (PDF/DOCX/PPTX/XLSX/EML/MSG) via the converter's naming convention. New `openProjectSource(absPath)` helper fetches via auth-gated `GET /v1/files/download` (which already serves PDFs as `application/pdf` for inline browser render, other formats as save-as), creates a blob URL, opens in new tab — bypasses the 'can't put JWT in query string' problem with native window.open. Right-side References panel renders project refs with extension-coloured tile (PDF red, DOCX blue, XLSX green, etc.) instead of the microlink screenshot. Inline ref-badges next to assistant messages get the same coloured-extension chip. The 'auto-open References panel on tool result' logic in the SSE handler is format-agnostic so it now opens for project tool results too. KV-cache invariant preserved (the system-prompt block is project-scoped; warm-pool slots only seed for agent=main, project=''). Test plan results now reproducible: Q3 (negative test) should refuse instead of fabricate; Q1/Q2 should show clickable PDF refs in the right panel for every cited document."),
    ("8.20.1", "2026-04-27", "Test-plan-found polish: cumulative per-folder triple counter + log file documentation. (1) Per-folder `triples_extracted` in sync_status.items now reads cumulative count from the KG (one COUNT() per resolved_prefix scoped by adapter_name='brain-project-kg') instead of last-cycle delta. The UI pill that says 'M triples' was previously showing 0 right after a cursor-skipped cycle even though the project still had all its triples — chip-level total_triples was correct, per-folder pill was misleading. New field `triples_last_cycle` records the per-cycle delta for debugging. UI unchanged (already reads triples_extracted). (2) Documented the launchd FD-redirect quirk: Brain's plist binds StandardOutPath/StandardErrorPath separately, but on macOS launchd both fd1 and fd2 actually map to ~/.brain-agent/server.error.log (verified via lsof). server.log gets only the startup banner. CLAUDE.md Deployment section + new memory note feedback_brain_log_file.md so future debugging hits the right file. No code change to the FD routing — the daemon prints are landing on disk, just in the file with 'error' in the name. Cursor + log tables in chats.db remain authoritative for monitoring."),
    ("8.20.0", "2026-04-27", "KG step-1 daemon hygiene — incremental closet regen, source-change KG invalidation, longer cycle interval. Three coordinated changes that make the project-sync daemon truly idempotent + cheap on unchanged content. (1) **Incremental closet wrapper**: new `kg_extract.run_closet_regen_incremental(...)` gates the wing-wide `mempalace.closet_llm.regenerate_closets` call on per-source `(mtime, size)` change detection. New `closet_regen_progress` cursor table in chats.db (PK: palace_wing + source_file). On every cycle the wrapper walks the wing's source files via `_iter_wing_source_files`, reads each on-disk file's stats, compares to cursor; if **any** source changed (or first-cycle), runs upstream regen for the wing and refreshes every cursor row; if all unchanged, short-circuits in milliseconds. With 400 unchanged PDFs the wrapper costs ~ms instead of 400 LLM calls/cycle. Daemon `_run_closet_regen_for(wing, source_prefix='')` swaps from naive call to wrapper; `regenerate_closets: true` is now daemon-safe. Sources without a real on-disk file (chat-sync mirror drawers etc.) are skipped from the cursor — they're handled by the chat-sync daemon's own closet path. Single-file regen would need an upstream `source_files=[...]` filter on `regenerate_closets`, deferred. New `closet_regen_purge_for_scope(wing, prefix)` for force-rebuild via the existing reextract endpoint. (2) **Source-change KG invalidation**: new `kg_extraction_source_state` cursor table in chats.db tracks each source's `(mtime, size)` per wing. `run_kg_post_pass(chunking_mode='source_file')` snapshots the cursor at start; for each iterated source, compares on-disk stats; on diff, calls new `_invalidate_source_in_kg(...)` which deletes `triples` rows matching that exact `source_file` (+ `adapter_name` filter when KG schema is 3.3.3+) and `kg_extraction_progress` rows for that source — orphan-entity cleanup runs after. The in-memory `already` set is shrunk so re-extraction proceeds on the new content. First-cycle entries record cursor without invalidating (nothing to purge yet). Without this fix, edited PDFs accumulated stale triples in the KG forever — old `source_drawer_id` no longer matched any drawer but `source_file` still pointed at the existing path, so queries returned mixed-version results. (3) **6-hour cycle interval**: `mempalace.project_sync.interval_seconds` default bumped from 1800 → 21600. Steady-state work was already incremental at every layer (`doc_convert` mtime/size hash skip, `mp_miner` content-hash dedup, `kg_extract` cursor, now `closet_regen` cursor) so 30-min walks were wasted overhead. Manual 'Sync now' from the project panel still triggers immediately on demand. New users get the longer interval automatically; existing installs keep their custom `interval_seconds` in config.json untouched. Both daemon-loop default sites updated (post-cycle wait + post-cancellation wait). New schema additions are idempotent ALTER-style (CREATE TABLE IF NOT EXISTS) so existing installs upgrade without migration."),
    ("8.19.1", "2026-04-27", "KG step-1 production validation + operational notes. End-to-end run on the German bank-policy PDF (Richtlinie-ZV-Vordrucke-2016, DK Zahlungsverkehrsvordrucke) post-restart with stabilised oMLX config: 46 chunks → 457 triples in 971.9s, 2 errors (4% chunk failure). KG totals across the test wing landed at 812 triples / 6 source files. Predicate distribution from this single PDF: 317 requires, 91 permits, 81 cites, 57 forbids, 53 defines, 39 condition, 13 applies_to, 11 exception, 7 penalty, 4 effective_from — ~98% controlled-vocab compliance. Source-language preserved end-to-end (German subjects/objects, English predicates). No code changes vs 8.19.0; this entry documents the production validation results and captures the operational tuning that made the local-model path work. Operational tuning (config.json — gitignored, set per-install): when running the local extraction model alongside the chat warmpool, set oMLX `providers.omlx.max_concurrent: 1` (serializes — was 2 for continuous batching, but two concurrent loaded models hit the 25.6GB process cap when 26B + e4b coexist) AND set 26B `warmup: false` so it isn't pinned in GPU memory between turns. Brain restart picks both up. Without these, e4b extraction fails with `HTTP 507 Insufficient Storage` because oMLX can't load it on top of the 26B (~22GB resident). With them, e4b loads on demand for extraction, evicting 26B; chat first-token latency goes up because 26B must be re-loaded from disk on the next chat turn — ~3-5s cold-load penalty. Cloud path (default `extraction_model: gemini-2.5-flash`) bypasses this entirely and is what the validation run used. Documented as a known-constraint footnote in CLAUDE.md so future debugging of 507s skips this rediscovery."),
    ("8.19.0", "2026-04-26", "Project knowledge graph (step 1) — LLM-driven document → triples extraction over project input folders + attachments. New kg_extract.py module: profile-driven extraction (`normative` for policies/regulations/specs/contracts/SOPs with controlled predicates requires/forbids/permits/defines/cites/applies_to/effective_from/supersedes/responsible_party/condition/exception/penalty; `generic` for open prose), source_file chunking mode that re-chunks at 3500 chars paragraph-aware (~70× yield improvement vs feeding MemPalace's 700-char drawer fragments 1:1 to the LLM — validated 430 triples from one German bank-policy PDF, 9.8 triples/chunk, 98% controlled-vocab compliance), per-drawer mode preserved as fallback. New cursor + log tables in chats.db (kg_extraction_progress keyed by `<rep_drawer_id>#<chunk_index>` for chunk-level idempotency, kg_extraction_log for run history). Triples persist via MemPalace's KnowledgeGraph (entities + triples) at <palace_path>/knowledge_graph.sqlite3 with TypeError-fallback for 3.3.0 schemas lacking source_drawer_id/adapter_name. Daemon hook in mempalace-project-sync runs after every mp_miner.mine() call, scoped per attachment hash and per input folder. New doc_convert.py: PDF/DOCX/PPTX/XLSX/EML/MSG → companion .md under <folder>/.brain-extracted/ pre-mine pass with idempotent (mtime, size) hash, paragraph-aware extraction, frontmatter source-anchor, stale-md sweeper, per-file isolation. xlsx extracts every row up to 100k/sheet (warn at 5k) since policy lookup tables ARE the policy. Three new agent-facing tools in the `memory` group: mempalace_kg_query (entity-first traversal with direction outgoing|incoming|both), mempalace_kg_search (predicate filter for contradiction/coverage analysis — e.g. predicate=requires + subject_contains=retention), mempalace_kg_neighbors (multi-hop BFS up to depth 3); all auto-scope to the caller's project via _thread_local.project, refuse outside project context (step 1 is projects-only). Five new HTTP endpoints under /v1/mempalace/kg: /stats (aggregate across accessible projects, admin sees all), /wing (per-project drilldown with predicate frequency, top entities by degree, sample triples, recent extraction-log), /entity (neighborhood for one entity), /extraction-log, /config (admin GET/POST). POST /reextract (admin or project owner) purges + queues sync. Settings → Knowledge Graph sub-tab: model picker (defaults to gemini-2.5-flash post-validation; GUI exposes the same picker for closet regen so one choice covers both), profile picker, max_triples / min_confidence / max_drawer_chars knobs, regenerate_closets opt-in checkbox, per-project KG cards with click-through to drilldown modal showing predicate frequency bars, top entities, sample triples, recent runs, admin re-extract button. Project Memory chip extended: shows 'Memory: N indexed · M triples', pulses purple while extraction running, double-click opens KG drilldown. Per-item pills (folder + attachment rows) gain KG sub-badge: '12 triples' (purple, indexed), 'KG…' (purple-pulse, in flight), 'KG !' (red, error). All wing access HTTP-side gated via _project_access_check; agent tools filter triples by source_file LIKE prefix matching project_dir + every input_folders[].path (resolved via os.path.realpath for macOS /tmp → /private/tmp symlink). Default extraction model set to gemini-2.5-flash because 26B chat warmpool + e4b extraction don't both fit under oMLX's 25.6GB process cap (host-level constraint, not a Brain limitation); local model can be selected when GPU headroom allows or warmup is off. Reasoning models need inference_max_tokens=8000 (default) to avoid mid-JSON exhaustion. Connection-refused retries 3× with 0.8s+2.0s backoff. New optional regenerate_closets: when on, daemon calls mempalace.closet_llm.regenerate_closets per project wing at end-of-cycle using the same model selected for KG extraction — boosts mempalace_query ranking by replacing MemPalace's regex closets with LLM-generated topic lines that capture implicit topics, foreign-language content, and contextual references; one LLM call per source file, default off. Multi-prefix scoping in every prefix-builder (daemon _run_kg_for, agent _kg_resolve_project_scope, HTTP _kg_resolve_project_from_query, _handle_kg_stats_global, _handle_kg_reextract, project sync's total_triples rollup) resolves symlinks so source_file matches what the miner actually stored. System-prompt nudge extended: tells the model that .brain-extracted/<name>.<ext>.md drawers came from a binary in the same folder — open the original with read_document for full fidelity. KV-cache invariant preserved (per-project block stays out of the warm-pool prefix). Validated end-to-end on Richtlinie-ZV-Vordrucke-2016 (DK Zahlungsverkehrsvordrucke): 44 chunks → 430 triples (191 requires, 55 cites, 53 permits, 33 forbids, 30 defines, 23 condition, 7 exception, 3 penalty), 950s elapsed, source-language preserved (German subjects/objects, English predicates)."),
    ("8.18.2", "2026-04-26", "Project-scoped MemPalace with input folders + per-item sync UI; ID-only wing scheme. Two big features and a chronic-bug cleanup. (1) Project input folders: each project's right-side panel grows a new section where a manager can pick on-disk folders via a stacked filesystem-browser modal (reuses GET /v1/files/tree with the existing IGNORE set, recursive vs top-level toggle). New project.json fields input_folders[] / input_folders_last_scan / sync_status. New endpoints: GET/POST/DELETE /v1/agents/<id>/projects/<name>/input-folders, POST .../sync-now, GET .../sync-status — all ACL-gated via _project_access_check (manage for write, read for status). New mempalace-project-sync daemon thread polls every 30 minutes (overridable via mempalace.project_sync.interval_seconds), walks each project's manual attachments (ingested/) plus every input folder, files into the project's MemPalace wing via the existing mp_miner.mine() pipeline. Path-traversal guard refuses paths inside agents/, /etc, /var, /usr, /bin, /sbin, /System, /Library/Keychains. Per-folder cap (max_files_per_folder, default 5000) skips runaway home-dir picks. Wakeup event fires immediately when a folder is added or 'Sync now' is pressed; live status flips to 'syncing' before mining begins so the UI reflects activity. Authoritative drawer counts via _count_wing_drawers_by_source / _count_wing_drawers_total — query Chroma directly at the end of each pass and persist sync_status.total_indexed (cumulative) plus per-item drawers_filed. Without this, every dedup-only re-run would clobber the count back to 0 (miner reports 0 new drawers when content hashes match, which is true on every cycle after the first). (2) Per-item sync UI: project header gets a Memory chip (idle/syncing/error states with pulse animation, click = Sync now); attachments section gets a status pill per file; input-folders section gets a status pill per folder. Pills repaint every 5s from /sync-status polling without re-rendering the lists (paintProjectItemPills walks data-pif-pill nodes), so DOM identity is preserved and hovering doesn't flicker. (3) ID-only wing scheme: dropped agent suffix and project name from MemPalace wings. Old: project__<name>--<agent_id>, user_id--<agent_id>, team_id--<agent_id>. New: project__<project_id>, user__<user_id>, team__<team_id>. Project IDs are uuid4 hex[:12], assigned on first read of project.json and persisted (so renaming the project doesn't strand its drawers, and two same-named projects under different agents never collide). _resolve_session_wing now returns '' for anonymous sessions instead of writing into the agent's namespace; chat-sync, _memorize_mempalace_turns, and _generate_session_summary all early-out on empty wing. _user_wing(uid) / _team_wing(tid) helpers. mempalace_query tool resolves project name → id via ProjectManager.get_project() and refuses to search if the id is missing rather than leaking. Visibility filter rewritten: project__* never appears in cross-wing searches, user__/team__ matched against the caller's identity, untyped wings treated as shared. Startup wipe in the project-sync daemon drops every drawer in any project__* wing and clears each project.json's sync_status — the daemon rebuilds from input_folders + ingested/ on its first cycle. _ensure_mempalace_yaml now compares the wing line in the file against the expected wing and rewrites on mismatch, so wing-scheme changes propagate without a manual yaml clean. (4) Orphan empty-session cleanup: ensureSession() in web/index.html previously pre-created a session row on every newChat(), every model-dropdown selection, and every PII auto-swap to give local models a head-start. The session-create endpoint kicks _trigger_warmup, but the resulting empty rows lingered in chats.db forever and showed up as 'Untitled' duplicates in the project chat list (the test project alone had 4 phantoms; org-wide we had 551 of these accumulated since the pre-create logic shipped). Fixed three ways: pre-emptive ensureSession() calls removed (warm-pool keeper + per-model warmup keeper still cover first-token latency without a session row), list_sessions filters sessions with 0 messages older than 60 seconds (the freshly-created one being typed into still shows), and a one-shot startup purge drops empty sessions older than 5 minutes. (5) Project chat input folders nudge: _build_system_prompt now lists every input folder by absolute path and explicitly tells the model that mempalace_query drawers carry source_file values RELATIVE to one of those roots, with a worked example showing how to join the absolute base + relative path before calling read_file/read_document. Without this the model would see source_file=screen.py from a drawer mined under /Users/alexander/Documents/dev/qb/ and try to read screen.py against the server's cwd. Plus a stronger memory directive: 'BEFORE answering ANY question that could draw on project knowledge — the user's documents, files in their input folders, facts they previously told you, project decisions — you MUST call mempalace_query first.' KV-cache invariant preserved for the user/team paths (the project block is per-project and warm-pool slots only seed for agent=main, project=''). Two new memory notes captured the wing-scheme rules and the orphan-session root cause for future sessions."),
    ("8.18.1", "2026-04-26", "Gemma 4 reasoning + thinking-block UX polish. (1) _detect_thinking_format gained gemma-4 / gemma4 to its oMLX reasoning-capable substring set — Gemma 4 ships with built-in thinking (enable_thinking kwarg + <|channel>thought channel-token output, AIME 88.3% / GPQA 82.3% with thinking on per the HF model card) and oMLX surfaces the channel-token output as delta.reasoning_content, so reasoning_field is the right classification. The CLAUDE.md comment that previously called gemma a non-reasoning model was written for Gemma 3 and never updated. (2) Latent persist bug fixed: init_models_config did dict(existing_models) — a shallow copy — so the per-model cfg dicts were aliased back to server.py's pre-init snapshot. The forward-looking thinking_format upgrade then mutated both sides, _models_differ saw matching values, and the persist gate skipped the save. The 8.18.0 forward-looking re-detect for cliproxyapi/gemini-2.5 only happened to land in config.json because of unrelated saves. Replaced the shallow copy with {k: dict(v) for k, v in existing_models.items()} so any in-place upgrade now actually persists across restarts. The three Gemma 4 entries (26B-A4B-it-MLX-4bit, e2b-it-4bit, e4b-it-4bit) auto-upgraded from 'none' to 'reasoning_field' on next server start. (3) Streaming thinking block now defaults to collapsed — the live render at renderStreamingMessage previously auto-expanded while thinking text was arriving and only collapsed once the answer started streaming. The header still shows 'Thinking...'/'Thinking' progress; click to peek at the chain-of-thought as it streams. The two finalized renderers (renderThinkingMessage history path + the inline assistant-message thinking) were already collapsed-by-default, no change needed there. KV-cache invariant preserved — no system prompt or warmup payload changes."),
    ("8.18.0", "2026-04-26", "Per-task thinking level + caveman mode for scheduled runs, format-aware UX everywhere. Two new schedules columns added by idempotent ALTER: thinking_level TEXT (''=inherit | none | low | medium | high) and caveman_chat INTEGER (0..3, response-style compression analogous to the chat composer toggle). Scheduler.add / Scheduler.update validate them; _execute_scheduled overlays thinking_level onto the resolved inference_params (or removes it on 'none' + sets thinking=False) before _run_delegate, and appends CAVEMAN_CHAT_PROMPTS[level] directly to the system prompt — same suffix the chat composer toggle uses. caveman_system is intentionally NOT exposed per task: it's a per-model knob tied to KV-prefix stability and would invalidate warmup. /v1/schedule add/edit endpoints accept the two new fields. Schedule create + edit modals get a 3-column row (Timeout · Thinking level · Caveman mode); the edit modal preselects the saved values. Format-aware option set everywhere via _thinkingOptionsForFormat / _thinkingOptionsForModel helpers in web/index.html: 'none' → '(unsupported)' disabled select; 'inline_tags' → Off/On (no graduated levels); 'mistral_blocks' → Off/High (provider only accepts those two); 'reasoning_field' / 'openai_opaque' → Off/Low/Medium/High. Schedule modal also gets an 'Inherit from model' entry on top, re-rendered when the model selector changes (preserves the user's prior choice when still valid). Models tab General Settings detail panel adds a Thinking Level dropdown next to Thinking Format — driven by the row's current format, re-renders on format change, persists inference.thinking_level (or omits when unset/disabled). Save path validates both per-row. Composer thinking-level button (#btn-thinking / #welcome-btn-thinking) is now format-aware: cycleThinkingLevel uses _composerLevelsForFormat to cycle only through valid steps for the current model (mistral_blocks cycles Off→High, inline_tags cycles Off→On, reasoning_field/openai_opaque keep the full Off→Low→Medium→High cycle). refreshThinkingButton self-corrects state.thinkingLevel in place when the user switches to a model that can't honor the saved level — every existing call site that already refreshes after a model change naturally enforces the rule. Tooltip now shows the cycle for the current format ('Thinking: medium (reasoning_field) · cycle: none → low → medium → high') so users can see why mid-levels aren't reachable on capped formats. Server-side belt-and-braces: new _validate_thinking_level_for_model helper rejects mismatches with helpful messages ('Mistral accepts only none or high', 'Model X does not support reasoning'); called from Scheduler.add (before INSERT) and Scheduler.update (cross-field check using the effective model — the one in the patch, or the existing row's). Empty model defers validation to runtime so 'Default' schedules still work. Detector fix: _detect_thinking_format gained an optional provider arg; cliproxyapi+gemini-2.5* and oMLX+qwen3/deepseek-r1/glm-zero/magistral now match even when the stored model id is bare (no scoped prefix). _match_known_model passes provider through. init_models_config does a forward-looking re-detect — when stored format is the conservative default 'none' but the provider-aware detector now returns a real format, upgrade in place; never the other direction (would clobber a deliberate user 'off'). Server startup persists the upgraded models when init produces a real change (was first-run-only before), so e.g. gemini-2.5-flash auto-upgraded from 'none' to 'reasoning_field' on this install. KV-cache invariant preserved — _build_system_prompt unchanged, scheduled tasks build their system prompt independently so the per-task caveman_chat append doesn't touch warm-pool prefixes."),
    ("8.17.0", "2026-04-26", "Per-user account settings + auto-maintained user profile (Memory from chat history). New User Settings modal split out from the global admin settings: clicking the username in the bottom-left sidebar opens a four-tab dialog (Profile, Memory, My Schedules, Security) — the gear button beside the theme toggle was redundant (already in the dropdown) and is now a chevron that toggles the dropdown. The dropdown's Settings entry was removed for non-admins; admins still see General settings as a separate item. Auth schema: new users.preferences JSON column (idempotent ALTER, validated keys greeting_name / job_description / communication_preferences / memory_chats_default / memory_sched_default / daily_summary_enabled / daily_summary_hour_local) with PREFERENCE_DEFAULTS + _coerce_pref(key, value) as the single source of truth — invalid values return 400 atomically without partial writes. New self-service endpoints POST /v1/auth/profile (display_name + email only — role/disabled stay admin-only) and POST /v1/auth/preferences (merge update, unknown keys silently dropped, default-valued keys cleaned out so the JSON stays small). Per-user defaults wired into the engine: chat creation in /v1/sessions reads memory_chats_default and overrides the server-wide classifier default for new sessions; the mempalace miner gates every sched-folder file on the schedule owner's memory_sched_default — explicit 0 skips, anything else (null/1/2) keeps the legacy 'always file' path. New schedules.user_id column + index records the creator; non-admins only see/edit/delete/run their own (admin sees everything; legacy rows with empty user_id stay admin-only on purpose). New _schedule_owner_check helper gates pause/resume/delete/run_now/edit/history/delete_run/clear_history; purge_orphan_history is admin-only. First-turn greeting preamble injected on the first user message (kept OUT of the system prompt so the warm-pool KV-prefix stays user-agnostic across all users) carries up to three lines: 'You are talking to <name>.' / 'Their role: <job>' / 'How they like to communicate: <prefs>'. Empty fields drop out, fully-empty preamble skips entirely. _greeting_injected sentinel is stripped by the existing _ALLOWED_MSG_KEYS filter so it never leaks to the wire. job_description capped at 500 chars; communication_preferences capped at 4000 chars (this field is the per-user equivalent of soul.md, intended for tone/voice/style guidance — large enough to fit a soul-style block, bounded so the per-turn preamble doesn't blow up the prompt budget). Both fields ship with inline 'Refine with AI' buttons in the Profile tab; /v1/refine extended with optional purpose='profile_field' (+ field_label) — uses a polish-don't-rewrite system prompt that preserves first-person voice and line breaks, skips chat-history context (privacy + irrelevance for bio polishing). After-refine the button flips to one-click Undo until used. Default refinement model in tools_config.json switched from cliproxyapi/gemini-2.5-flash (silently echoed input verbatim — bad for any polish prompt) to mistral-vibe-cli-fast which actually follows the polish rules (tested round-trip across job-desc, comm-prefs, and chat-prompt rewrites). Auto-maintained 'Memory from chat history' (the Claude.ai 'Gedächtnis aus Chat-Verlauf generieren' equivalent): one Markdown file per user at agents/main/user_profiles/<uid>.md mirrored as one drawer per ## section into MemPalace (wing=<uid>--main, room=user_profile, source_file=user/<uid>#profile/<slug>). Sections in fixed order: Work context, Personal context, Top of mind, Recent months, Earlier context, Long-term background. File is the source of truth; mirror is purge-then-add so renamed/removed sections don't linger. Atomic write via tmp + os.replace, with versioned history at <uid>.history/<ISO-timestamp>.md (capped at 30 entries; intentionally KEPT on Reset so users can recover from a hasty rebuild). New _user_profile_dir / _user_profile_path / _read_user_profile / _write_user_profile_atomic / _delete_user_profile / _split_profile_sections / _mirror_user_profile_to_mempalace / _purge_drawers_by_room_and_source / _purge_user_profile_drawers helpers — all module-level so HTTP handlers and the daemon share them. New user-profile daemon thread (replaces the v8.14.0 daily-summary path which built activity logs of dubious value): polls every 30 min, gates on daily_summary_enabled + local-hour match + 23h cooldown, fires once per local day per opted-in user. Per-user worker (_profile_run_synchronous) pulls 100 most-recent chats from the last 90 days, samples title + first user msg + last assistant msg (250 chars each, total input capped at 12K chars), feeds them through engine._run_delegate with the fixed-schema _PROFILE_SYSTEM_PROMPT — hard rules: never invent, third-person voice, match the user's predominant language, edit existing profile in place rather than rewrite, demote stale 'Top of mind' to 'Recent months' when no fresh evidence appears. Refinement model resolved via _profile_pick_model (refinement → cheapest → server default), GDPR auto-fallback to local on PII findings via gdpr_pick_model_for_background. New self-service endpoints GET /v1/auth/profile-doc (content + cursor + enabled flag), POST /v1/auth/profile-doc (manual edit, 32KB cap), POST /v1/auth/profile-doc/update-now (synchronous regen — same logic as the daemon worker, ~5-60s), POST /v1/auth/profile-doc/reset (delete file + drawers, keep history dir). Account Settings → Memory tab gains an editable Markdown textarea (380px tall, monospace) with Update now / Reset / Save buttons + status line showing last-run timestamp + bytes + 'no_activity' / 'filed' / 'error' state. send_message round 0 reads the profile file (capped 4KB) and prepends as a separate '[Auto-maintained user profile (from chat history; treat as background context, not as ground truth for the current request):...]' block on the first user message of each session — distinct block from the user-set greeting/job/comm-prefs preamble so the model can tell user-set guidance apart from inferred long-form context. Module-level _gather_user_chat_samples builds the per-chat samples; module-level _user_profile_run_llm wraps the delegate call with proper thread-local cleanup in finally. One-shot startup purge of the deprecated user_daily_summary room across every user wing on every server start (idempotent — second call is a no-op once the room is empty); was 0 on this install since the v8.14.0 daemon never actually fired due to the local_hour gate. Welcome view greeting now reads 'Good morning, Alex' instead of 'Good morning' — refreshWelcomeGreeting() is auth-aware (greeting_name → display_name → username, falls back to anonymous 'Good morning' when no auth or pre-login) and re-runs from renderUserMenu() so login + profile saves update it without a page reload. Schedules My Schedules tab shows the user's owned tasks with state badges + link back to the Scheduled view. Cleanup: all bare AGENTS_DIR references in server.py replaced with engine.AGENTS_DIR (latent bug — the helpers were inside main() so the bare name resolved nowhere; would have crashed if exercised, but try/except wrapping made it silent). Module-level imports added: datetime, sqlite3 (one was duplicated). KV-cache stability invariant preserved: _build_system_prompt remains user-agnostic (the user-greeting injection from an earlier iteration was reverted because it broke warm-pool prefix matching for every authenticated turn — now lives in the first-user-message preamble where it costs nothing across users)."),
    ("8.16.0", "2026-04-25", "Scheduled-task attachments + working directory. Two pieces of context the agent previously had no way to receive on a scheduled run: file attachments (durable, picked once, referenced on every fire) and a per-task working directory (the cwd the agent should pass to execute_command). DB: new schedules.attachments (JSON list of {name,path,mime,size}) + working_dir (TEXT) columns added by idempotent ALTER in Scheduler._init_db. Engine: _execute_scheduled appends a disk-files notice listing the stored absolute paths so the agent reads them in place with read_document/read_file — no per-run /tmp copy. working_dir overrides the 'Current working directory:' line in the system prompt and adds a directive to pass it as cwd= to execute_command; python_exec stays pinned to the artifact folder by design (file-write tracking depends on it). Cleanup: Scheduler.remove() reads the attachments JSON before DELETE and rmtrees each per-upload uuid folder under agents/<agent>/scheduled_attachments/<uuid>/; Scheduler.update() diffs old vs new attachment paths and purges orphans (chip removal in the edit modal frees bytes). _purge_attachment_paths() refuses to touch anything outside scheduled_attachments/ as a guard against malformed metadata. New POST /v1/schedule/upload (multipart, auth-gated) saves files under agents/<agent>/scheduled_attachments/<uuid>/<name> and returns {name,path,mime,size}. /v1/schedule add/edit accept attachments[] and working_dir. GET /v1/files/tree gains an empty-path default of $HOME so the folder picker has somewhere to start without leaking $HOME via a separate endpoint. UI: showCreateScheduledModal() and _schedViewEdit() got an attachments file picker (uploads on selection, removable chips with kb size + filename) and a read-only working_dir input + Browse… button + clear (×). Browse opens a stacked folder-picker modal (z-index above the schedule modal): breadcrumb of the current absolute path + scrollable list of subfolders, ↑ .. row to go up, click any folder to descend, Select this folder writes the path back to the input and closes only the picker. Hidden files and the usual junk (.git, node_modules, __pycache__, dist, etc.) are filtered by the existing /v1/files/tree IGNORE set; only entries with type === 'dir' show up. _schedUploadFiles sends Authorization: Bearer <token> from localStorage('auth-token') and uses BASE_URL — bare fetch hit the global /v1/* auth gate and 401-ed before the handler ran. Dropped an earlier per-run copy that would have churned /tmp on every fire (50MB CSV × daily = 18GB/year for no benefit) — the source path under scheduled_attachments/ is durable and on the same filesystem, so the agent reads in place; only delete-time cleanup is needed."),
    ("8.15.1", "2026-04-25", "oMLX continuous-batching opt-in + speed-profile resource-knob fix. Two related changes. (1) Bumped oMLX provider seed max_concurrent from 1 → 2 in config defaults. Re-benchmarking on gemma-4-26b-a4b-it-4bit (Apple Silicon) showed oMLX *does* fuse concurrent /chat/completions into batched forward passes (continuous batching) — earlier 8.9.0 prose claiming 'GPU serializes internally' was wrong for oMLX (still correct for CLIProxyAPI). Numbers: batch=1 → 63 tok/s decode, 2.3s TTFT; batch=2 → 80 tok/s aggregate, 4.3s TTFT; batch=4 → 91 tok/s aggregate, 8.6s TTFT. 2 is the sweet spot for multi-user / parallel-tool / warmup-overlap workloads — 27% aggregate throughput gain for ~40% per-request decode slowdown when both slots are full. CLAUDE.md Provider Concurrency Queue section rewritten to reflect this and document the per-provider tradeoff. (2) Removed `warmup: True` from the `speed` MODEL_PROFILES overlay (claude_cli.py). The profile system is supposed to set request-style defaults (warmup_mode, parallel_tool_calls, caveman_system, deferred_tool_groups, compact_threshold), not resource knobs. With warmup in the overlay, toggling warmup off in the Models tab on a speed-profile model would be silently re-enabled at next resolve_model_settings() — surprising and unfixable from the UI. Web UI saveModelsConfig now persists `warmup: false` explicitly (rather than deleting the key), so an explicit user-off survives even if a future overlay drift re-introduces a warmup default. Memory note feedback_profile_overlay_resource_knobs.md captures the rule for future profile edits. No schema change, no API change — config-only."),
    ("8.15.0", "2026-04-25", "Autonomous MemPalace artifact ingestion. The mempalace miner daemon (server.py) is rewritten end-to-end. Old behavior: read flat sources[] from config, refuse to run without a hand-written mempalace.yaml in each source dir, mine everything indiscriminately — result on this install was 392 sessions × 0 drawers because (a) no yaml had ever been created and (b) the chat-sync default_mode=0 means every session started with save_to_memory=0. New behavior: (1) auto-discover every agent under AGENTS_DIR; for each agent walk agents/<id>/artifacts/ and split folder names <date>_<sid_prefix> into sched (sid_prefix starts with sched-) vs chat. (2) Sched folders: file only output-role files via tool_add_drawer using extension-based classification (skip _ARTIFACT_INTERMEDIATE_EXTS = py/sh/js/ts/json/yaml/csv/log/etc), source_file=session/sched-<run>#artifact/<name>. Sched chat content (reasoning, tool calls) deliberately stays out — there are no sched-* rows in messages for chat-sync to mirror. (3) Chat folders: gate on parent session save_to_memory>0; when ON, ensure a mempalace.yaml exists in that folder (auto-written with brain-managed marker, never a manual step) and run mp_miner.mine() over it. When OFF, skip silently — keeps drawers from leaking past the per-chat toggle. (4) New helper _purge_orphan_chroma_queue runs once at startup: detects segments missing a max_seq_id bootstrap (= compactor never knew where to start) and deletes their >24h-old embeddings_queue rows. Cleared 323 stale closet entries from this install — those had been pinned to a bootstrap-less HNSW segment since 2026-04-19 with no path to compaction. (5) Two new ChatDB helpers: session_memory_modes() returns {sid: save_to_memory} in one query (cycle-scoped, no per-folder DB hit), session_id_for_prefix() resolves 8-char folder prefixes to full session ids via LIKE lookup. (6) launchd plist now sets PYTHONUNBUFFERED=1 so daemon prints reach server.log immediately instead of buffering indefinitely under launchd stdout redirection — that buffering is why the old miner's 'No mempalace.yaml found' errors had been invisible for weeks. Wing convention is <agent_id>_artifacts so future per-agent scoping queries work without schema change. Recursive folder walk not yet implemented — sched-task subdirectories like 2026-04-23_sched-75/artifacts/ are currently top-level-only; revisit if it bites."),
    ("8.14.0", "2026-04-25", "Per-user cost quotas + Plan-usage pill. New QuotaManager (claude_cli.py) with role-based limits (admin / poweruser / user), per-user overrides, and a billing-cycle window (monthly / weekly / yearly) with start-day clamping for short months — Feb-30 anchors fall back to the last day of the month, weekly anchors are 0=Mon..6=Sun, yearly anchors are month-of-year. Two axes per user: rolling-day (resets at UTC midnight) and cycle (resets on cycle anchor). Three enforce modes via config.json → quotas.enforce_red: (a) warn_only (default) — pill goes red, requests still allowed, no server-side refusal; (b) force_local — silently swap the request to the configured default_local_fallback_model when the user's worst axis hits red, audit-logged as quota_force_local; (c) hard_block — raise QuotaExceededError pre-LLM, audit-logged as quota_blocked. Local models always bypass the gate (is_model_local check). Schema migration: added user_id column + (user_id, created_at) index to cost_log; _log_call_cost now captures user_id from _thread_local.current_user_id. New CostTracker methods sum_user_window / per_model_user_window power the cycle queries. Pre-flight gate fires inside send_message on _tool_round == 0, after the GDPR check, so quota refusals don't burn tool-loop tokens. New endpoints: GET /v1/quotas/me (any user; returns daily + cycle state with reset timestamps and worst-axis level), GET/POST /v1/quotas/config (admin), GET /v1/quotas/admin/users (admin; every user's level + daily + cycle), GET /v1/quotas/admin/breakdown?user_id=X&days=N (admin or self; per-model breakdown for current cycle plus daily 30-day series). Existing /v1/costs and /v1/costs/daily extended with optional ?user_id=X scoped by ownership. Web UI: status-bar Plan-usage donut (#status-quota) — small SVG arc tinted green/yellow/red by worst axis, label shows the higher of (daily_pct, cycle_pct), hides when limits are zero. QuotaMonitor polls /v1/quotas/me every 30s and refreshes after each turn ends. Click opens an anchored popover (position:fixed, right-aligned to the pill, two-frame measure-then-position so the bottom edge stays on-screen) with daily + cycle bars, reset countdowns, role + override chips, and a mode-aware footer that explains what happens on red. New Settings → Quotas tab (admin only): cycle config + warn/block thresholds + enforce-mode dropdown + local-fallback-model picker (filtered to enabled local models) + per-role limits table (admin/poweruser/user × daily_usd/cycle_usd) + org-wide user list with level chips, per-user 'Set override' prompt, and per-user 'Details' modal showing per-model + last-30-days table. Removed: legacy max_session_cost_usd / cost_limits machinery (status-bar warning triangle, 70%/90% modal, Server-tab cost-input field) — the per-user quota system replaces it; Server tab now points to the Quotas tab via a 'Configure →' link. Backfill: pre-existing cost_log rows have user_id='' and are reassigned via session_id → sessions.user_id where the chat row still exists (~$0.20 attributable here), with all remaining unattributed rows (deleted sessions, scheduler synthetic sessions, background non-chat LLM calls — classifier, summariser, next-prompt, warmup) assigned to the org admin. Audit row 'cost_log_backfill' records the bulk reassignment so it's traceable. Stages 2+3 (per-user GDPR compliance reporting + harmful-prompt analytics) deferred."),
    ("8.13.0", "2026-04-24", "Client-hosted local inference + server-local routing shortcut. Two complementary changes to the execution-mode story. (1) Air-gap-mode optimization: when execution_mode=client, requests to server-local models (oMLX, CLIProxyAPI, any RFC1918-base-URL provider) now skip the browser proxy entirely and run server-side as they would in normal mode — saves a full round-trip per turn on every local-model request. The is_model_local(model) check was added to the existing proxy gate in send_message; tool-call proxying (web_fetch, exa_search) is unchanged since those are about internet access, not inference. (2) New client-hosted local inference path: desktop clients (Electron) can declare they serve a model family locally, and the server transparently transfers matching requests to the client for execution — queue-free per-user inference, works in both server and client execution modes, stays local on air-gapped deployments. Independent of execution_mode: the decision is per-request based on the session's capability handshake, not a global toggle. Scheduler tasks, delegates, and background calls never reach this branch because they don't populate the client_capabilities thread-local. Server side: new config.json → client_models: [{id, family, gguf_path, sha256, size_bytes, auto_download}] declares GGUF weights available to desktop clients. GET /v1/client/models/manifest (any auth user) lists entries with absolute paths stripped. GET /v1/client/models/<id>/weights streams bytes with HTTP Range support for resumable downloads, X-Model-SHA256 response header for self-verification, audit-logged on first chunk (start==0) so range resumes don't flood. POST /v1/client/models (admin-only) CRUD with server-computed sha256 + size_bytes on every save so the manifest stays truthful even if the GGUF file changes. New config.json → client_engines: {darwin-arm64, win32-x64, linux-x64 → {url, sha256}} published via GET /v1/client/engines — admins point URLs at an internal mirror; server refuses to invent defaults so misconfigured air-gap deployments never silently fetch from public internet. Session.client_capabilities in-memory field (dict, never persisted) populated via POST /v1/sessions/<id>/capabilities ({enabled, families: [...]}); unknown families are silently dropped server-side (cross-checked against the manifest). Engine routing in send_message reads client_capabilities from thread-local (plumbed by chat worker before send_message_with_fallback), calls is_model_client_executable(caps, model) resolver to match model-id → manifest-entry → family → capability, and emits a new local_inference_request SSE event that reuses ProxyChannel for SSE response streaming. Fallback policy: on client failure the error is surfaced; no server-side retry (explicit design decision — retrying would double latency on failures). Every transfer writes a client_inference audit row with args_summary='model=X family=Y'. New POST /v1/chat/local-inference-usage endpoint lets the client report token counts back to the server after a client-hosted turn; logged to costs.db with provider='client:<sid>' and cost=0 (electricity on the user's laptop is not our problem) so dashboards can distinguish client- vs server-executed turns without schema changes. Desktop Electron app (desktop/local-inference.js, new ~470 LOC module): fully lazy — no binary bundled, no weights bundled, no auto-downloads on launch. Cache under userData/brain-local-inference/{engine,models}/ keyed by sha256; state.json persists engine_sha. Resumable http/https downloader with streaming sha256 verification, .partial sibling files, Range requests, restart-from-zero fallback when server refuses a range, abort signal support. Engine manifest fetched from Brain server on first use; llama-server binary downloaded once and chmod+x on Unix. llama-server spawned on random free localhost port (pickFreePort via net.createServer listen on 0), waitForEngineReady polls /health, 10-min idle SIGTERM→SIGKILL timer, model swap triggers respawn. runInference POSTs OpenAI /v1/chat/completions with stream=true and forwards raw SSE lines to renderer via local-inference-chunk IPC events. Graceful shutdown on app.before-quit — no orphaned child processes. preload.js exposes electronAPI.localInference.{status, ensureEngine, ensureModel, run, cancel, onChunk, onEnd, onError, onProgress, removeListeners}. Web UI: new LocalInference module in web/index.html replaces the Phase-3 stub — FIFO queue via Promise chain (max_concurrent=1, matches llama.cpp single-GPU reality), ensureEngineAndModel gates first use, handleRequest streams llama-server SSE back to /v1/chat/proxy-response. Capability handshake fires on session open + reopen + after toggle save (so changes take effect without re-creating sessions). Two new General Settings tabs: Client Models (admin-facing, manages the manifest with add/delete form + per-row badges showing family/size/sha + read-only engine-manifest view of /v1/client/engines per platform) and Local Inference (per-installation preference, master toggle + per-family checkboxes + Download now prefetch button with live progress bar driven by onLocalInferenceProgress callback). Composer chip ('local' pill in accent colour next to model selector) shows when the current model will route to client-hosted inference — updates on every model switch via refreshLocalInferenceChip(). Auth posture: manifest reads + weight downloads any authenticated user, CRUD admin-only, capabilities + usage scoped to session owner or admin. Known limitations: engine archives (zip/tar) not yet supported — admin must publish a direct llama-server binary URL per platform; usage reporting lane currently placeholder since llama-server's streaming protocol doesn't cleanly expose end-of-turn token counts beyond the SSE usage chunk the server already ingests through the proxy channel."),
    ("8.12.0", "2026-04-24", "Granular GDPR settings — dedicated Settings → GDPR tab replaces the 4-checkbox card. Scanner rules are now grouped into 8 semantic categories (secrets, national_id, national_id_ctx, financial, contact, network, personal, bare_id) with per-category actions (ignore / warn / block). New PII_RULE_CATEGORIES + PII_DEFAULT_CATEGORY_ACTIONS maps in claude_cli.py are the single source of truth, mirrored as PIIScanner.ruleCategories / defaultCategoryActions in web/index.html. Per-rule overrides (rule_overrides: {rule_id: action}) win over category actions. Every finding carries a category + effective action; _pii_scan_text skips 'ignore' rules entirely (no scan, no log). New _pii_effective_action helper resolves rule_overrides > category > default, and downgrades 'block' to 'warn' when server_block (master switch) is off — keeping back-compat for existing configs. New _pii_worst_action helper returns block > warn > ignore across findings; main-chat send_message and gdpr_pick_model_for_background now refuse only when the scan's worst action is 'block' rather than on any finding, so warn-only categories no longer raise RuntimeError. Email allowlist: every email finding is matched against gdpr_scanner.email_allowlist (list of full addresses or '@domain' patterns, case-insensitive); matches are suppressed entirely — no finding, no audit row, no modal. Server validates on POST /v1/services/server: unknown rule_ids are rejected (typos surface), unknown categories silently dropped, actions must be ignore|warn|block, allowlist entries must contain '@' and no whitespace. Web UI: dedicated GDPR tab in General Settings (alongside Server/Models/…) with three sections — master switches (enabled, server_log, server_block, fallback model), email allowlist textarea (one per line, '@domain' for wildcards), and collapsible category rows showing rule counts + override counts + inline action dropdown per category. Expanding a category reveals per-rule override selects (empty = use category default). Single Save button commits everything; Reset-to-defaults restores category actions without touching master switches or allowlist. Server tab now shows a one-line GDPR status chip (active / disabled / hard-block on) with a Configure → button linking to the new tab. applyGdprConfigToScanner() is the single entry point that syncs PIIScanner.policy + state.pii* from the server response; called from the startup fetch, the Server tab refresh, and after every GDPR save. piiBlockActive / piiHistoryWorstAction now gate on scan.worstAction === 'block' instead of any finding, so composer model-filtering only kicks in for true block-severity findings."),
    ("8.10.0", "2026-04-23", "GDPR local-model routing — the long-standing follow-up to v8.8.0's scanner. When the outgoing payload contains personal data the chat is automatically routed to a local model instead of being blocked at the door. Three layers: (1) config.json → gdpr_scanner.default_local_fallback_model — a model id used by every non-interactive LLM call to transparently swap cloud → local when PII is detected. Configurable in Settings → Server → GDPR card (dropdown is populated only with enabled local models; selection is validated server-side against is_local + enabled). (2) Server-side hook gdpr_pick_model_for_background(model, texts, purpose) in claude_cli.py — single decision point called by generate_next_prompt_suggestion, classify_chat_for_memory, _summarise_tool_result (worker subagents), _run_delegate (delegate_task tool + scheduler tasks + agent-to-agent delegation), _generate_chat_summary. Every detection at the background layer emits a pii_detected audit row; every swap emits an additional pii_auto_fallback row; a new pii_blocked row fires when server_block=true and no local fallback is usable. New GDPRBlockedError sentinel (inherits RuntimeError) propagates refusal cleanly: next-prompt/classifier return None, summariser returns the static fallback summary, delegate returns a 'Delegation error: [GDPR block]...' string, chat summary skips. (3) Client-side interlock in web/index.html — piiBlockActive(chat) is true when the composer draft OR the loaded chat history contains PII (new piiHistoryText/piiHistoryHasFindings walk the messages array with a cache keyed by message count, invalidated on openSession / user-message push / stream done / newChat). When active, toggleModelDropdown reduces the model list to is_local-only with an amber 'Personal data detected' header; piiEnsureLocalModel auto-swaps the chat to the configured fallback (or first enabled local) the instant PII is seen; sendMessage refuses-with-toast if server_block is on and no local model is selectable. Badge shows three distinct states: red when no local available, green when routing via local (with 'auto-selected' marker on first swap), amber warn-only when block is off. New badge scope label 'Personal data earlier in this chat' distinguishes history-derived findings from fresh draft input. Server-side send_message block gate now checks is_model_local(model) before raising the user-facing RuntimeError — previously the gate fired unconditionally and would refuse even when the user had already picked a local model, so manual recovery from the dropdown was broken (session b2703917 regression). GET /v1/models/config now annotates every model entry with is_local (derived from _is_local_base_url on the resolved provider) so the web UI doesn't duplicate URL parsing. New is_model_local(model) helper in claude_cli.py is the single authoritative resolver. All call sites verified: scanner runs on both main chat (round 0, warn-or-block) and every background path (detect + swap + optional refuse); audit trail covers detect, swap, and block independently."),
    ("8.9.0", "2026-04-23", "Per-provider concurrency queue for local LLM gateways. Local runtimes (oMLX on port 8000, CLIProxyAPI on 8317) can't actually process two /chat/completions in parallel even when multiple models are loaded — the GPU serializes internally and a second request stalls the first. Without coordination, two concurrent chats + scheduler delegates + warmup primes fought for the same wire and occasionally got 500s. New LocalProviderQueue (claude_cli.py) with per-provider semaphore + strict-FIFO waitlist, opt-in via config.json providers.<name>.max_concurrent (0 = unlimited = no queue). Seeded: omlx=1, cliproxyapi=2, cloud providers stay 0. Wrapped every HTTP call site that hits a /chat/completions endpoint: send_message main chat, _run_delegate, run_model_warmup, classify_chat_for_memory; generate_next_prompt_suggestion and _summarise_tool_result are covered transitively via send_message_with_fallback. Warmup goes through the queue too (label=warmup), so the keeper can't cut in front of a live chat. Slot is held only during the HTTP wire time — _handle_openai_response calls release_slot() the instant the SSE stream drains, before any tool execution or recursive send_message, so local tool work (exa_search, python_exec, worker summariser) doesn't block other chats from the gateway. Worker subagents that fire a nested summariser LLM call now just queue normally — no re-entrancy, no deadlock. Cancellation during wait removes the ticket from the deque cleanly; cancellation mid-HTTP fires via existing cancel_token path. New GET /v1/queue/status exposes per-provider active + waiting tickets (label, model, session, agent, age). New POST /v1/queue/cancel (admin-only, audit-logged as queue_cancel): waiting tickets raise TaskCancelled(\"Queue cancel by admin: <reason>\") on their next 200ms poll tick; running tickets have their cancel_token fired (same signal as the per-chat Stop button). SSE events queue_wait / queue_acquired / queue_released stream to the UI per turn. Web UI adds a status-bar Queue pill next to Pool — always visible when any provider has max_concurrent > 0, label shows N/M (active/capacity) or N+W/M when queued. Click opens a modal listing active + waiting tickets per provider with live updates; admins see a red Cancel button per row. Per-turn inline banner in the chat streaming bubble: 'Waiting in queue on <provider> — position N of M · Xs' until queue_acquired fires. QueueMonitor polls /v1/queue/status (1s fast when tickets exist, 10s slow when idle)."),
    ("8.8.0", "2026-04-23", "GDPR / PII pre-submit scanner. Every chat message and text attachment is scanned locally in the browser before it leaves the client, and again server-side before hitting the LLM. Zero external APIs, free, offline. 71 regex-based detectors across three tiers: (1) Tier 1 national IDs with real checksums — UK NINO + NHS (mod-11), NL BSN (11-proef), BE national number (mod-97), PL PESEL, PT NIF, SE personnummer (Luhn), DK CPR, NO fødselsnummer (dual mod-11), CH AHV (EAN-13), CZ rodné číslo, RO CNP, HU TAJ, GR AMKA, BG EGN, IE PPS, ES DNI/NIE, IT Codice Fiscale, DE Steuer-ID, FR INSEE, AT SVNR, US SSN, BR CPF + CNPJ, CA SIN (Luhn), MX CURP, AR DNI, IN Aadhaar (Verhoeff), JP My Number, KR RRN, SG NRIC, TW national ID. (2) Tier 2 cloud secrets — AWS access key + secret, GitHub PAT + app tokens, Slack tokens + webhooks, Google API key + OAuth client, Stripe live/test, OpenAI, Anthropic, Twilio, SendGrid, Mailgun, JWT, Azure Storage connection strings, Azure account keys, PEM private keys, basic-auth in URL, entropy-gated generic `api_key = \"...\"` assignments. (3) Tier 3 context-fallback — fire on keyword + number-shape even when checksum fails, so `SVNR: 3030201077` and `svr-nummer: ...` trigger regardless of validity. Plus a bare-identifier heuristic for messages dominated by ID-shaped number lines (the classic 'what is this number?' paste). Strict checksum rules win first via overlap suppression; context-fallback and bare-identifier rules catch what checksum-strict would miss. Single source of truth in two mirrored implementations: PIIScanner in web/index.html (JS) and _pii_rules/_pii_scan_text/_pii_scan_bare_identifiers in claude_cli.py (Python) — 58/58 parity on handcrafted positive fixtures, 0 false positives on prose / non-Luhn card-shaped numbers. Redesigned warning modal: 640px amber-gradient banner with animated shield icon, hero-style count, per-source cards with pill badges and redacted monospace samples (first 2 + last 2 chars visible, rest masked), keyboard shortcuts (Esc cancels, Cmd/Ctrl+Enter sends), click-outside dismiss, session suppression checkbox. Composer inline badge redesigned as an amber gradient pill with shield SVG and formatted count. Server-side mirror runs in send_message on _tool_round == 0 only (subsequent rounds replay the same user content); findings logged to audit.db as pii_detected rows when gdpr_scanner.server_log is enabled; optional hard-block mode raises pre-LLM when gdpr_scanner.server_block is true. New Settings → Server → GDPR / PII Scanner card with three checkboxes (enabled / server_log / server_block) and Save. Config at config.json → gdpr_scanner, cached 30s, invalidated on save via engine._invalidate_gdpr_cache(). Future: auto-route PII-containing requests to local-only models (blocked on multi-user inference queue)."),
    ("8.6.0", "2026-04-20", "Warmup overhaul — first-response latency drops from 15s to 2-3s on local models. Root cause was a silent KV-cache miss: the warmup prime payload didn't match the real first-turn payload byte-for-byte, so oMLX's prompt cache never hit. Four drift sources fixed: (1) system prompt contained minute-precision timestamp that differed between warmup and real request — rounded to the hour in _build_system_prompt so prefixes stay byte-stable across request boundaries; (2) warmup payload omitted MCP tools that the real payload includes — warmup now attaches the process-global MCPManager and merges mcp tools into the sorted tool list; (3) warmup used stream=False vs the real request's stream=True+stream_options — now identical; (4) per-session _trigger_warmup in server.py was a divergent copy of the warmup payload — deleted, replaced with a thin delegation to engine.run_model_warmup so both paths share one code path. Also fixed: run_model_warmup now bumps last_warmup_ts on failure so a perpetually failing model (OOM Qwen 35B) doesn't monopolize the max_concurrent=1 keeper slot via oldest-first sort. New per-model warmup_mode config (\"full\" default | \"minimal\"): full primes the KV prefix (system+tools), minimal loads weights only — trade-off is user's call, no auto-selection. Keeper re-primes when mode flips, won't evict warm models otherwise. Config changes now wake the keeper via a threading.Event so new warmup flags take effect immediately instead of waiting up to 30s. Pool invalidation extended to any KV-prefix-relevant field change (warmup, warmup_mode, enabled, max_context, warmup_allow_cloud, parallel_tool_calls, caveman_system, provider, base_model_id). New UI: status-bar Pool indicator with aggregate ready/target + failed count; click opens a modal listing each warmup-enabled model with state badge, progress bar, mode chip (full/minimal), last-warmed age, last_error, and per-model 'Warm now' button. Modal body live-refreshes via WarmupMonitor._render(). Models tab detail panel adds a Warmup Mode dropdown. Log format: [warmup-keeper] <model>: warm (<mode>, <ms>ms)."),
    ("8.5.0", "2026-04-20", "Thinking blocks, direct providers, fixes. Thinking feature fully wired: per-model thinking_format field (none/inline_tags/reasoning_field/mistral_blocks/openai_opaque) auto-detected from model id; engine parses all four non-opaque formats from the stream and emits thinking_start/delta/done events; each round of reasoning becomes its own role='thinking' message row so the transcript preserves chronological order (user → thinking → tool calls → next round thinking → ... → final answer). UI toggle disables itself when the selected model has thinking_format='none' so the button stops lying. Opaque reasoning (OpenAI o-series, Mistral Small 4 via Bifrost) renders a 'Thought for N tokens' badge from usage.completion_tokens_details.reasoning_tokens. _InlineThinkingSplitter handles <think>…</think> tags that span SSE chunk boundaries (14/14 boundary unit tests pass). Bifrost rip-and-replace: removed Bifrost from Brain's provider list; added four direct providers (omlx → http://localhost:8000, cliproxyapi → http://localhost:8317, mistral-experimental and mistral-vibe → https://api.mistral.ai). Motivation: Bifrost's ChatContentBlock Go struct silently drops reasoning_content (oMLX) and nested thinking arrays (Mistral) during re-serialization, so thinking text was never reaching the client. Routing preserves scoped model ids (OMLX/*, Bifrost/mistral/*, mistral/*) via base_model_id so 125 existing sessions keep working unchanged. Tool-loop fixes: diminishing-returns guard stops the loop when the last 2 rounds each added <500 completion tokens from round ≥3 (prevents plateau-spin); tool-call dedup is now session-scoped (keyed by session_id with 1h TTL) instead of thread-local, so worker subagents and ThreadPoolExecutor batches can no longer miss duplicate calls; parent thread-local context (session_id, agent, mcp_manager, user_id) is now propagated into worker threads. UI fixes: streaming DOM flicker resolved — tool_call/tool_result and all worker.* handlers now call renderStreamingMessage(chat) after renderMessages() so the in-flight thinking panel + partial text aren't wiped when tools fire; thinking panel renders live during streaming (previously only a wave-bars placeholder showed, the actual reasoning appeared only at end-of-turn); tool-round badges removed from thinking headers (matched Claude.ai / ChatGPT / Le Chat norm). Worker tool references: _extract_web_references in execution.py pulls title/link/domain out of raw exa_search/web_fetch output before the artifact is written, and attaches them as envelope.references so the References panel populates again for worker-wrapped web tools. Reload interleave: tool_round stamped on every tool_call / tool_result SSE event and persisted into metadata.tools[i].tool_round; reload path buckets tools by round and interleaves with thinking rows so session restore shows thinking → tools → thinking → tools → assistant in correct chronological order."),
    ("8.4.1", "2026-04-19", "Caveman mode icon set. The composer caveman toggle now shows a distinct icon per level instead of color-coding a single icon: off = spaceship, lite = car, full = horse, ultra = campfire. Makes the primitive↔modern axis obvious at a glance. Implementation is cavemanIconFor(mode) in web/index.html, wired through updateStatusBar(). Tooltip and toast reflect the new metaphor."),
    ("8.4.0", "2026-04-19", "Per-turn MemPalace control. Chat drawers are now addressed per turn (source_file=session/<sid>#turn/<user_msg_id>) so individual Q&A pairs can be memorised or purged independently. New per-assistant-message Memory menu with 8 actions: memorise complete chat / this response / all above / all below, and the matching four removes. Items auto-grey when inapplicable (already memorised, nothing to remove, chat memory mode ≠ off). New session-manage actions memorize_turns and purge_turns (both accept turn_ids[] or {scope, anchor_turn_id}). New GET /v1/mempalace/session-turns returns the set of memorised turn ids for UI greying. Session-inspector turn header now carries two extra badges — thinking level (none/low/medium/high) and caveman mode (sys+chat with clear names: off/lite/full/ultra) — per turn, reflecting state at the time the turn ran (new turns only; historical turns stay honest and omit them). Models tab Caveman System input is now a dropdown with clear names instead of 0–3. Disabling chat memory after content was stored prompts the user: OK hard-purges the session's drawers from MemPalace, Cancel just stops filing new turns."),
    ("8.3.0", "2026-04-19", "Chat memory state + MemPalace activity feedback. Reopening a chat now correctly restores its memory mode (off/on/auto) from the session record — previously only the current default was applied, so chats saved to memory appeared as 'off' after reopening. /v1/sessions/<id>/messages now returns save_to_memory alongside caveman_mode. Composer icons refreshed: caveman toggle is now a campfire (flame + crossed logs), save-to-memory is a classical palace (columns + pediment) — both clearer metaphors than prior abstract shapes. New live activity animation: the palace icon pulses blue when MemPalace is retrieving (mempalace_query tool call) and green when storing (background chat-sync loop or immediate-sync on toggle). Backed by a thread-safe activity tracker in claude_cli.py (mempalace_activity) exposed via GET /v1/mempalace/activity; web UI polls every 2s. Worker badge now reliably appears on chat tool blocks via permissive substring detection (previously missed some envelopes due to JSON.parse failures on truncated results) — matches session inspector behavior. Right panel toggle moved from the bottom status bar to the page header (top-right), so opening/closing the panel no longer requires a cross-screen mouse travel."),
    ("7.9.1", "2026-04-18", "Right panel toggle and auto-open. Status bar 'Panel' button opens/closes the unified right panel (Attachments, References, Files) without clicking an item in chat. Auto-open: new web references from tool results open the References tab automatically, new/updated artifacts always switch to the artifact (even when a different one was selected). Clicking a reference badge in chat history opens the References tab and scrolls to + highlights that specific source card. Panel toggle button shows active state when open. Badge counts update in real-time during streaming."),
    ("7.9.0", "2026-04-18", "Python code execution environment + parallel tool calls. New python_exec tool runs Python in a sandboxed subprocess with the session's artifact folder as working directory — files written by scripts auto-register as artifacts. Large stdout (>1K chars) auto-saved as artifact with head+tail preview replacing full output in context (token savings). New code_exec tool group — agents opt in via token_config.tool_groups. Configurable timeout, max output, venv path in tools_config.json. Middleware _middleware_pyexec_hint detects 3+ chained file/doc tool calls and injects a one-shot hint to consolidate into python_exec. tools.md updated with python_exec guidance: when to prefer over tool chains, available packages (docx, openpyxl, pptx, reportlab, PIL, csv), document processing examples, output rules. Parallel tool calls: parallel_tool_calls parameter now sent in API payload (default true), per-model toggle in Models tab. Web UI toolDescribe mapping for python_exec."),
    ("7.8.0", "2026-04-18", "Built-in tool group deferral — rarely-used tool groups (email, documents, code_graph, scheduler) are excluded from LLM requests by default and loaded on-demand when the model calls tool_search. Saves ~1,760 tokens per LLM call (~26% of tool definitions). System prompt tells the model which groups are deferred so it knows to discover them when needed. Configurable per-agent via deferred_tool_groups in token_config. Web UI Tokens tab adds per-group 'defer' checkboxes with amber styling, DEFERRED badges in the tool breakdown, and a savings summary banner showing effective token cost. tool_search added to the core tool group to ensure it's always available when group filtering is active. Save Token Config now merges into existing config instead of overwriting (preserves mcp_tool_filter and other fields)."),
    ("7.7.1", "2026-04-17", "Split caveman mode into two independent settings. System-level caveman (per-model in Models tab config, 0-3) compresses the system prompt itself — prepends a compression prefix and applies rule-based text reduction (strip whitespace, remove filler words, collapse markdown, remove examples at higher levels). Chat-level caveman (per-session toggle in composer, 0-3) controls response style as before. User's last chat caveman level is persisted to localStorage and auto-applied to new sessions. Both modes compose independently: system-level sets baseline prompt compression, chat-level appends response style instruction."),
    ("7.7.0", "2026-04-17", "MemPalace reliability + smart memory gating. Fixed client proxy SSE line-splitting that silently dropped tool calls in proxy mode (TCP chunk boundaries splitting data: lines). Fixed save_session wiping user_id on every message (INSERT OR REPLACE → ON CONFLICT preserving existing values). Fixed per-user wing separator (/ → -- to comply with MemPalace sanitize_name). System prompt updated to reference mempalace_query instead of old memory_store tools. New save_chat_to_memory tool lets the model explicitly save a conversation when the user asks. New LLM-based chat sync classifier gate — configurable model classifies each message pair as fact/preference/decision/reference (file) or generic/refusal/chitchat (skip) before filing to MemPalace. Configurable min_turns threshold skips short throwaway chats. Three-state memory toggle per session: on (green, save all), auto (amber, classifier decides), off (grey, skip). Default mode for new chats configurable in Settings → MemPalace. Connection health monitor with status bar indicator (green/red dot, 10s polling). Status bar visible on all views. Retry on transient MemPalace query errors. Settings UI for classifier model, min turns, file categories, and default mode."),
    ("7.6.0", "2026-04-16", "Client execution mode for air-gapped corporate deployments. When the server has no internet but browser clients do, set execution_mode to 'client' in config.json or Settings → Server. LLM inference calls are proxied through the browser: server emits proxy_request SSE events with the full payload, browser calls the provider's /chat/completions endpoint, streams chunks back via POST /v1/chat/proxy-response. Web-accessing tools (web_fetch, exa_search) are similarly proxied via POST /v1/chat/proxy-tool-result. All local tools (file ops, git, shell, code graph, mempalace, etc.) continue executing on the server. ProxyChannel class in claude_cli.py provides thread-safe queue bridging between the agentic loop and browser. Configurable proxy tool list in Settings GUI — any tool can be routed through the browser. Session inspector shows purple CLIENT badge on turns executed via proxy. Status bar shows CLIENT badge when mode is active. Provider credentials relayed to browser via GET /v1/config/execution-mode. Requires CORS-enabled LLM providers (Mistral API, OpenAI API, Bifrost local proxy all confirmed working). Chrome is the primary supported browser."),
    ("7.5.0", "2026-04-16", "Per-user MemPalace memory isolation, admin dashboard, and session delete cleanup. Wings now use user_id/agent_id format so each user's chat memories are isolated by default — shared wings (brain_code, mined source) remain globally visible. mempalace_query auto-scopes to the current user: bare agent names are prefixed with user_id, unfiltered searches post-filter to exclude other users' wings. user_id propagated via _thread_local to chat workers and delegate tasks. Chat sync writes user-scoped wings; sessions without a user_id (pre-auth, system) fall back to bare agent_id. Session.user_id field added, loaded from DB on restore, set from auth at creation. New MemPalace admin dashboard tab in General Settings — overview stats (drawers, closets, wings, rooms, edges, DB size), knowledge graph counts, daemon status, per-wing breakdown with user-scoped vs shared badges and room chips, tunnel list, write-ahead log activity with operation badges, and anomaly detection (sparse wings, stale sync, disabled daemons, high drawer/closet ratio). GET /v1/mempalace/stats endpoint aggregates data from MemPalace collections, graph, KG, WAL, and chat_mempalace_sync cursor table. Session delete now purges associated MemPalace drawers and closets (background thread matching source_file prefix session/<sid>) and cleans up the sync cursor row — archive leaves memories intact."),
    ("7.4.0", "2026-04-16", "MemPalace direct integration — replaces the MCP stdio server with in-process Python imports. Single built-in mempalace_query tool (hybrid BM25+vector+closet search) replaces ~15 mcp_mempalace_* tools. Two background daemons in server.py: mempalace-miner (auto-mines source tree + artifacts every 30 min) and mempalace-chat-sync (mirrors chat turns, session summaries, attachment metadata, and web references into MemPalace drawers every 60s with closet rebuilds). No manual 'mempalace mine' runs needed — palace stays up to date automatically. Config in config.json 'mempalace' block with mine sources, chat_sync roles/tool allowlist, and closet toggle. New 'memory' tool group in DEFAULT_TOOL_GROUPS. chat_mempalace_sync cursor table in chats.db tracks sync progress. Also fixes newChat() not clearing chatTitle (stale title from previous session shown on new chats)."),
    ("7.3.0", "2026-04-15", "Purge B: api_type parameter fully removed from all function signatures. Follow-up to v7.2.0 Purge A. send_message, send_message_with_fallback, _retry_with_backoff, _run_delegate, _compact_conversation, _check_and_compact, ContextManager.recall/summarize_chunk/check_and_compact, make_headers, get_available_models, _apply_inference_to_payload, list_models, _handle_openai_response — all lose the api_type parameter. resolve_provider_for_model now returns {api_key, base_url, provider_name} (no api_type). Session.api_type field removed. server_config['api_type'] removed. CLI --api-type flag removed from both claude_cli.py and server.py argparse. _delegate_api_type global removed. All attachment routing branches in server.py _handle_chat collapsed to the single OpenAI image_url path (Anthropic document blocks gone). Warmup path in server.py simplified. Net effect: ~30 signatures cleaned up, ~60 call sites updated, provider config schema simplified. All providers are OpenAI-compatible (Bifrost, Kilo); re-adding an Anthropic-direct provider would require reintroducing wire-format branching."),
    ("7.2.0", "2026-04-15", "Token optimization, cost guardrails, and Anthropic/Mistral wire-format purge. Status bar now shows last-round prompt tokens (not cumulative across tool rounds) so 'context used' reflects the real next-call size. Hard tool-round cap at 1.5× the soft limit stops runaway loops. Per-agent runtime limits in agent.json 'limits' block: max_tool_rounds, tool_result_char_limit, tool_results_total_tokens, context_safety_ratio. Pre-flight context guardrail raises before provider 400s when estimated prompt exceeds max_context * safety_ratio. Session cost soft warnings: 70% amber triangle, 90% red + one-time modal per session, configurable global max_session_cost_usd in Settings → Server → Cost Limits. Built-in cost rate table expanded: OpenAI (gpt-4o/4.1/o1/o3/o4-mini), Mistral, Gemini, Grok, DeepSeek, local-model zeros. New GET /v1/tools/breakdown endpoint measures per-group + per-tool token cost with schema decomposition (name/description/schema). Tokens tab shows the breakdown with heavy-schema ⚠ markers. MCP improvements: redundant '<server>_' prefix stripped from tool names (mcp_mempalace_mempalace_search → mcp_mempalace_search, ~260 tok saved on mempalace alone), '[MCP:server]' description prefix dropped, per-agent MCP tool filter/exclude in token_config with fnmatch globs, UI checkbox-save directly from breakdown view. Removed prompt_caching token_config field (no-op outside Anthropic wire format). Purge A of Anthropic/Mistral wire formats: _handle_anthropic_response (224 lines), _handle_mistral_response + mistral SDK helpers (291 lines), _collect_anthropic/_stream_anthropic, and all api_type branching in send_message/_run_delegate/make_headers/get_available_models/_apply_inference_to_payload/list_models collapsed to OpenAI-only paths. ~600 lines removed. api_type parameter still threaded through signatures (Purge B pending). All providers are OpenAI-compatible (Bifrost, Kilo)."),
    ("7.0.0", "2026-04-14", "Native agentic loop restored. The v6.0.0 PI Agent SDK unification is reverted: the Node.js pi_sidecar process, the Anthropic Agent SDK sidecar (sdk_sidecar.py / sdk_backend.py), and all Mistral SDK paths are removed. Chat, delegate, scheduler, refine, and soul_chat all flow through the native Python agentic loop (send_message / _handle_openai_response / middleware pipeline). The Lossless Context Manager (ContextManager + context.db) is back as the compaction engine — SSE compacting/compacted events and the /v1/context/* endpoints work again. Interpretation B: all providers are OpenAI-compatible (Bifrost, Kilo). api_type is always 'openai'; Anthropic and Mistral wire formats are gone. Code mode is removed from both UI and server (was tied to PI). The /v1/tools/call and /v1/hooks/run endpoints are deleted — Brain tools run in-process through _execute_tool() which fires hooks natively. Net diff: server.py -1100 lines, pi_sidecar/ directory deleted. See stage commits dcf73a4..30468b1."),
    ("5.14.0", "2026-04-10", "Unified provider resolution and multi-provider model support. Single resolve_provider_for_model() function in claude_cli.py is now the sole source of truth for model→provider credential resolution — used by interactive chat, PI sidecar (code mode), SDK sidecar, _run_delegate (summaries, auto-memory, scheduled tasks), warmup, and all background LLM calls. Provider-scoped model keys (provider/model_id) support multiple providers offering the same model with different API keys. Mistral model discovery via SDK (client.models.list()) instead of broken raw HTTP. Models tab: All/None buttons per provider for bulk enable/disable. Orphaned models from deleted providers cleaned up on sync. Code mode model dropdown fixed (dropdown-menu class, consistent with chat mode). get_api_model_id() resolves scoped keys to actual API model IDs across all call sites."),
    ("5.13.0", "2026-04-10", "Dynamic attachment routing — file attachments are now routed based on model capabilities instead of browser-side MIME type splitting. New per-model raw_formats config (MIME patterns like image/*, application/pdf) controls which files are sent as multimodal content blocks vs saved to disk for read_document parsing. Server-side unified handler merges body.images and body.files, checks model's raw_formats, and routes accordingly. Vision-capable models see image pixels directly; text-only models get metadata or vision model description via attachment_image_model fallback. Web UI unified: all files go through single _pendingFiles path with image thumbnail previews. Models tab shows editable raw_formats per model. Settings hint warns when no vision capability is configured. Guards: 20MB inline size limit, OpenAI/Mistral restricted to image/* multimodal, Anthropic PDF via document blocks."),
    ("5.11.0", "2026-04-08", "Provider-level SDK routing and auth hardening — Use SDK setting moved from per-agent config to per-provider config. Smart routing: anthropic providers have a configurable use_sdk toggle (default on), mistral always uses Mistral SDK natively, openai always uses direct agentic loop. Provider edit/add forms show the toggle only for anthropic type. Fixed web UI authentication: all raw fetch() calls in agent settings tabs (Soul, Agent, Skills, Memory Health, MCP, Tokens) and Code Mode now use API helper with auth headers — previously these tabs returned empty data when auth was enabled."),
    ("5.10.0", "2026-04-08", "Per-model settings UI and thinking model auto-recovery."),
    ("5.9.0", "2026-04-07", "Chat file attachments — attach files directly in the chat composer. Files are saved to a session-scoped temp directory on the server; the agent reads them on demand via read_document (PDF, DOCX, XLSX, PPTX, CSV) or read_file (text/code). Web UI: file input accepts 30+ extensions, binary formats (PDF, DOCX, XLSX, PPTX) sent as base64, text files as UTF-8. File preview chips in composer with remove buttons. Attached files shown on sent user messages with extension badges. Configurable vision model for image attachments (Settings → Server → Attachments). Documents tool group added to default agent config."),
    ("5.8.0", "2026-04-06", "Model management overhaul — models now have a configurable display_name (default = cleaned model ID). All UI surfaces show models as 'displayName (provider)' format — selectors, dropdowns, status bar, spinners, agent config. Models tab redesigned: grouped by provider with collapsible sections, sorted by display name, inline editable display names, per-model remove button. Manual model add form for providers without /models endpoint (model ID + provider + display name with datalist autocomplete). Code mode dropdown uses modelsConfig instead of flat model list. Provider tab badges use compact names. Backend: display_name field added to _match_known_model()."),
    ("5.7.0", "2026-04-03", "Token optimization suite — comprehensive per-agent token usage controls via new Tokens tab in agent config. Tool group filtering sends only relevant tools to the LLM (13 groups: core, memory, context, web, email, documents, delegation, code_graph, git, scheduler, mcp, skills, nodes). System prompt trimmed: tools.md reduced from 1500 to 400 tokens, memory summary cap configurable per agent. Anthropic prompt caching via cache_control on system prompt blocks. System prompt cached per-session (60s TTL) to avoid disk I/O on tool loops. Memory summary scheduled tasks restricted to memory-only tools (4 instead of 39). Compact threshold configurable per agent. SDK duplicate tools cleaned up. Kilo API provider added (OpenAI-compatible gateway). Context fill bar and manual compact button in chat footer. Background pipeline model selectors with fallback in Memory tab GUI. Fixed SDK token count inflating context display (was reporting API tokens_in instead of conversation estimate)."),
    ("5.6.0", "2026-03-31", "Code Mode overhaul — full-featured coding assistant experience. Folder browser GUI for project selection (breadcrumb navigation, lazy-loaded directory listing via /v1/files/tree). Fixed SSE streaming (two-line event:/data: format matching server output). Tool calls display identically to main chat (gear/check icons, expandable args/results, proper .open toggle). Streaming indicator with wave animation, model name, tool labels, elapsed timer, and stop button. Folder-based project system — sessions tagged with folder path, 'All Projects' expands to show discovered projects with session counts, selecting a project filters sessions. Session management: archive and delete buttons on hover in sidebar. Code mode sessions properly scoped by folder path via project field."),
    ("5.5.0", "2026-03-31", "Claude.ai-style Projects — full project workspace system modeled after Claude.ai. Projects list view with card grid, search, Your Projects/Archived tabs, sort by activity/name/created. Project detail view with back navigation, description (show more/less), chat composer, conversation list, and right panel with Instructions and Files sections. Custom instructions per project — editable via modal, stored in project.json, injected into system prompt for all project conversations. File upload via multipart form (replaced deprecated cgi module with manual boundary parser for Python 3.13+). Files displayed with document icons, deletable per file. Project-scoped conversations — sessions filtered by project field, new chats auto-scoped. Project CRUD — create modal with name/description/agent, archive (preserves data + QMD), delete (soft-delete to .trash, removes QMD collection). Context menus on project cards and chat items. API: GET/POST /v1/sessions?project=X for project-filtered session listing."),
    ("5.4.0", "2026-03-31", "Artifact system — Claude.ai-style artifact management. Files written with relative paths auto-land in agents/<name>/artifacts/<session_folder>/. Session-scoped SQLite registry with content snapshots (versioned blobs, up to 5MB per version). Resizable right panel with type-aware rendering: syntax-highlighted code (highlight.js), sandboxed HTML iframe, inline SVG, image display, rendered markdown. Version selector dropdown, copy/download/source-toggle actions. Artifact cards in chat messages (coral border, monitor icon) open panel on click. Artifacts excluded from QMD indexing and knowledge graph (not memory). Artifacts browse view in sidebar: full-page grid with content preview cards, type filter tabs (All/Code/HTML/Documents/Images/Markdown), agent filter chips, time-ago timestamps. Click-through from browse opens chat + artifact panel. API: GET /v1/artifacts, /v1/artifacts/browse, /v1/artifacts/<id>/content, /v1/artifacts/<id>/download."),
    ("5.3.0", "2026-03-31", "Claude.ai-style web UI + interactive agents. Complete UI rewrite: sidebar + multi-view layout (Welcome, Chat, Chats, Projects, Knowledge Graph, Customize) with Anthropic Sans/Serif/Mono fonts, warm light/dark themes. Tool call blocks show with full args during streaming and persist across page reloads (reconstructed from assistant message metadata). Tool display toggle works. Interactive mode: agents can ask clarifying questions via AskUserQuestion — sidecar intercepts with PreToolUse hook, emits user_input_needed SSE event, blocks until answer arrives via POST /answer/{query_id}. TUI renders questions with selectable options. New endpoints: memory CRUD, soul.md AI editing, MCP registry. Agent creation accepts model and display_name. Sidecar captures tool input_json_delta for full args on content_block_stop."),
    ("5.2.0", "2026-03-29", "Mission Control cockpit — the web UI is now a dashboard-first design inspired by mission control interfaces. Agent cards show live status, model, schedules, past actions (scrollable), projects, and cost. Chat and project views are full-screen modals with maximum screen space. Token Cost Feed table with per-agent breakdown. Consistent color palette (dark navy header, light cards, green/orange/purple accents) across cockpit, chat modal, config dialogs, and settings. Session cache for instant cockpit loads. Hover actions for archive/delete on sessions and projects. Team badges on agent cards. Agent ordering matches team hierarchy (main → teams → standalone). All chat input controls (attach, think, plan, tools toggle, refine) available in modal."),
    ("5.1.0", "2026-03-28", "Real-time streaming + Claude Code skills. Sidecar rewritten as REST API (POST /query, GET /events/{id}) — decouples event production from consumption for true token-by-token streaming. MCP tools served via /mcp JSON-RPC endpoint on the server. Hooks moved server-side into /mcp tools/call handler (SDK hook registration was the root cause of streaming buffering). Claude Code plugin integration: scan, browse, install, and toggle 121 CC plugins per agent via GUI. SDK integration audited against official docs: @tool decorator, allowed_tools wildcards, correct hook signatures."),
    ("5.0.0", "2026-03-28", "Full SDK migration complete — closed all remaining gaps from the Agent SDK transition. HTTP MCP server for 24 custom tools, chat summary + transcript indexing for SDK path, file change watcher, rate limiting + model fallback, trace spans + audit logging, all background tasks route through SDK, TUI + CLI one-shot + scheduled tasks route through sidecar with graceful direct-API fallback."),
    ("4.5.0", "2026-03-27", "Agent SDK integration — all agents now use the Anthropic Agent SDK (Claude Code) as the agentic loop backend. Multi-provider support: Claude via CLIProxyAPI (Max subscription), MiniMax, oMLX local models, and Gemini via CLIProxyAPI. Real-time token streaming via a lean sidecar process. SDK badge in status bar and message footers. Provider-aware env var routing. System prompt extracted into reusable _build_system_prompt()."),
    ("4.4.6", "2026-03-26", "Token consumption guardrails — base64 image data is stripped from tool results after processing, individual tool results are capped at 30K chars, accumulated results are compressed when they exceed 50K tokens, mid-turn compaction runs every 3 tool rounds, and CLIProxyAPI gets a tighter 8-round tool limit to protect the OAuth quota. Telegram dedup guard prevents duplicate sessions from 409 polling conflicts."),
    ("4.4.5", "2026-03-26", "File previews now support images (JPEG, PNG, GIF, WebP, SVG) and office documents (DOCX, XLSX, PPTX, PDF, CSV). Sidebar chat attachments show preview and download buttons, file counts are accurate from first load, and the list is stable across re-renders."),
    ("4.4.4", "2026-03-26", "Fixed sidebar file attachments disappearing when the accordion was opened — files created in a chat session are now fetched from a dedicated endpoint that includes the full message history, so compacted messages no longer hide previously created files."),
    ("4.4.3", "2026-03-26", "Fixed cost tracking showing $0 for all models — auto-discovery was writing cost_input=0 to config, which silently overrode the built-in Anthropic rate table. Config zeros are now treated as unset and fall through to correct rates. Added missing model IDs (Haiku 4.5, Sonnet 4.0, Opus 4.0) to the built-in table, plus prefix patterns as a catch-all for future versions. Historical costs for the past week were retroactively corrected."),
    ("4.4.2", "2026-03-26", "Smarter memory I/O — the nightly background pipeline now reads only what it needs from memory files instead of loading everything into RAM. The autodream consolidation run also reuses a single memory scan across all its passes, reducing redundant filesystem work."),
    ("4.4.1", "2026-03-26", "Significant token savings — background tasks that only produce structured output (memory deduplication, conflict detection, relationship discovery, context summarization) no longer receive the full tool schema on every call, saving ~8,000 tokens per invocation. Smart fallback chains route time-sensitive tasks to fast cloud models and nightly tasks to free local models."),
    ("4.4.0", "2026-03-26", "Reduced Anthropic token consumption across the board — shorter context windows, a capped memory summary injected only at the start of each turn, earlier context compaction, and configurable model selection for all background pipelines. GUI model selectors added for memory summary, relationship discovery, and autodream."),
    ("4.3.1", "2026-03-25", "Stability fixes — resolved a session corruption issue when model fallback was triggered mid-tool-loop. Rate limit and overload errors from the API now retry gracefully instead of switching models immediately. Shell commands now run in a login shell so your PATH and environment are always available."),
    ("4.3.0", "2026-03-24", "Autodream — a nightly memory consolidation pipeline that automatically deduplicates overlapping memories, flags stale ones, detects contradictions, and identifies reusable procedures worth turning into skills. Results are summarised in a health report with scoring. A new Memory Health dashboard in Settings shows per-agent stats and recall frequency."),
    ("4.2.0", "2026-03-23", "Smarter code graph — the knowledge graph now generates plain-English summaries for every function and class, classifies code into architecture layers (API, service, data, UI, util, test), and produces a guided reading order. New context fill indicator and manual compaction controls added to the chat footer."),
    ("4.1.0", "2026-03-23", "Chat reliability improvements — conversations no longer get corrupted when a tool loop fails mid-way. Partial responses are preserved on cancel or error. Message metadata (model, tokens, cost, thinking) is now persisted and restored when reopening a chat. Thinking depth can be controlled (off / low / medium / high)."),
    ("4.0.0", "2026-03-23", "Universal file support — agents can now read, write, and edit Excel, PowerPoint, CSV, images, and SVG files in addition to PDF and Word. A full code structure graph powered by Tree-sitter covers 14 languages, with tools to query relationships, trace call chains, and analyse the blast radius of any change."),
    ("3.7.0", "2026-03-23", "Extensible hooks — attach shell scripts to any tool call or file write event. Hooks can inspect, block, or react to agent actions without modifying core code. A hooks UI in agent config makes wiring them up straightforward."),
    ("3.6.0", "2026-03-22", "Lossless context — long conversations are no longer truncated into a flat summary. A DAG-based hierarchy preserves the full conversation tree, letting you search and drill back into any compacted segment with context_search and context_recall."),
    ("3.5.0", "2026-03-22", "Full-text chat search across all sessions, with semantic search via QMD and SQLite fallback. Knowledge graph relationship discovery upgraded to a two-stage pipeline — QMD finds candidates, an LLM classifies the actual relationship type."),
    ("3.4.0", "2026-03-22", "Remote nodes — Brain Agent can now distribute tasks to other machines running node.py. Nodes are managed from Settings with configurable tokens, allowed tools, and concurrency limits. macOS launchd install/uninstall built in."),
    ("3.3.0", "2026-03-22", "Richer projects — AI can directly edit notes using the same file tools it uses everywhere else. Chat transcripts are indexed for semantic search. Sessions now show LLM-generated summaries in the sidebar, and files created during a conversation appear as downloadable attachments."),
    ("3.2.0", "2026-03-22", "Project Notes — a full note-taking system inside Brain Agent, with a rich text editor, AI chat sidebar per note, and automatic knowledge graph integration. Notes live alongside agent memory and are searchable."),
    ("3.1.0", "2026-03-21", "Memories now form automatically from conversations — corrections, decisions, and references are detected and stored without prompting. The knowledge graph gains a visual canvas view. Sidebar redesigned around Projects and Chats. Continuous session summarisation added."),
    ("3.0.0", "2026-03-20", "Major platform expansion — provider fallback with retry, backup and restore, webhook and email notifications, full observability tracing, dynamic MCP client connections, image upload and vision support, and a multi-messaging adapter framework."),
    ("2.1.0", "2026-03-20", "Agent workflows — define multi-stage pipelines in YAML with optional human approval gates between stages. Web UI sidebar moved to the left with a consolidated status bar. Mobile layout improved."),
    ("2.0.0", "2026-03-20", "Projects — organise work into scoped contexts. Ingest PDFs, Word docs, web pages, and watched folders. The knowledge graph tracks relationships between documents and memories, and chat history is scoped per project."),
    ("1.7.0", "2026-03-20", "Plan mode for read-only review before executing. Web search results are cached to avoid redundant calls. Cost tracking and rate limiting added per agent. Custom slash commands and LLM-powered input refinement."),
    ("1.6.0", "2026-03-20", "TUI reaches feature parity with the Web UI — 30+ slash commands, autocomplete popup menus in both interfaces."),
    ("1.5.3", "2026-03-20", "Concurrency hardening — thread-safe agent context, per-collection QMD debouncing, YAML-safe memory frontmatter, hash suffixes on memory filenames to prevent collisions, concurrent scheduler execution."),
    ("1.5.2", "2026-03-20", "Fixed memory summary refresh and QMD index path normalisation. QMD collection health stats surfaced in the settings UI."),
    ("1.5.1", "2026-03-18", "MiniMax provider support. Add Model UI. Fixed a QMD session leak and an issue where shared memory returned only metadata instead of full content. Telegram bot runs in-process."),
    ("1.5.0", "2026-03-18", "Settings dashboard — a full admin panel for Server, QMD, Models, Telegram, and Providers. Agent activity indicators, QMD document browser with per-file index health, smart model routing, and a self-healing QMD index keeper."),
    ("1.4.0", "2026-03-17", "QMD hybrid memory search (BM25 + vector + LLM reranking) replaces SQLite FTS5. Improved SSE error handling and server resilience."),
    ("1.2.0", "2026-03-16", "Multi-provider routing, Gmail integration, scheduler dashboard, SQLite resilience improvements, Cloudflare Zero Trust deployment."),
    ("1.1.0", "2026-03-14", "MCP support — connect any MCP server via stdio or SSE transport, scoped per agent or globally."),
    ("1.0.0", "2026-03-14", "Async agent delegation — agents run in background threads with task status tracking and cancellation."),
    ("0.9.0", "2026-03-14", "Skills system — SKILL.md files loaded on demand, available per agent or globally."),
    ("0.8.0", "2026-03-14", "Multi-agent system — define agents with soul.md personalities, delegate tasks between them, switch with /agent."),
    ("0.7.0", "2026-03-14", "Persistent memory — store and recall information across sessions with per-agent isolation."),
    ("0.6.0", "2026-03-14", "Context window management with automatic compaction when the window fills."),
    ("0.5.0", "2026-03-14", "Full tool suite — file read/write/edit, shell execution, web search, and fetch."),
    ("0.4.0", "2026-03-14", "Escape to cancel in-flight requests. Dynamic terminal rendering and startup greeting."),
    ("0.3.0", "2026-03-13", "Exa web search with agentic tool-use loop."),
    ("0.2.0", "2026-03-12", "Interactive TUI with spinner, markdown rendering, and model switching."),
    ("0.1.0", "2026-03-10", "Initial release — streaming chat, model fallback, SSE parsing."),
]

import collections
import json
import re
import threading
import time
import urllib.request
import urllib.error


# ---------------------------------------------------------------------------
# Forward stubs — these are populated/overridden by claude_cli.py at runtime.
# When this module is imported standalone (e.g. for testing), the stubs
# provide safe defaults so module-level code doesn't crash at import time.
# ---------------------------------------------------------------------------
_models_config: dict = {}
_current_agent = None
_thread_local = threading.local()
_mcp_manager = None


DEFAULT_MAX_CONTEXT_TOKENS = 131072


def get_model_max_context(model: str) -> int:
    """Return the model's context window size, or DEFAULT_MAX_CONTEXT_TOKENS."""
    return (_models_config or {}).get(model, {}).get("max_context", DEFAULT_MAX_CONTEXT_TOKENS)



# --- Web Result Cache ---

class WebCache:
    """Thread-safe LRU cache with TTL for web results."""

    def __init__(self, max_entries: int = 200, ttl: int = 900):
        self._cache = collections.OrderedDict()
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.ttl = ttl
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> dict | None:
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self.misses += 1
                return None
            ts, value = entry
            if time.time() - ts > self.ttl:
                del self._cache[key]
                self.misses += 1
                return None
            self._cache.move_to_end(key)
            self.hits += 1
            return value

    def put(self, key: str, value: dict):
        with self._lock:
            if key in self._cache:
                del self._cache[key]
            self._cache[key] = (time.time(), value)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)

    def clear(self):
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict:
        with self._lock:
            return {
                "entries": len(self._cache),
                "max_entries": self.max_entries,
                "ttl": self.ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / max(1, self.hits + self.misses) * 100, 1),
            }


_web_cache = WebCache()


# --- Plan Mode ---

READONLY_TOOLS = frozenset({
    "read_file", "list_directory", "search_files", "web_fetch", "exa_search",
    "memory_recall", "memory_shared", "task_status", "list_nodes",
    "context_search", "context_detail", "context_recall", "schedule_list",
    "schedule_history", "use_skill", "gmail_inbox", "gmail_read", "gmail_search",
    "read_document",
    "code_graph_build", "code_graph_query",
})

PLAN_MODE_PROMPT = (
    "\n\nPLAN MODE ACTIVE: You are in read-only planning mode. "
    "You may ONLY use read-only tools (read_file, list_directory, search_files, "
    "web_fetch, exa_search, memory_recall, memory_shared, task_status, etc.). "
    "Do NOT attempt to write files, execute commands, store memory, send emails, "
    "or delegate tasks. Instead, describe a detailed plan of what you WOULD do, "
    "including specific file paths, commands, and steps.\n"
)

# Default body for the per-project Instructions field. Used when project.json
# has no `instructions` value set — the v8.22.0 anti-hallucination disciplines
# ship as the out-of-the-box content so every project gets sane defaults
# without the owner having to know about them. Project owners can replace,
# tighten, or remove any rule via the project Instructions textarea in the
# right panel; the literal text below is what they will see pre-filled when
# they first open the editor (see `_get_project_instructions`).
#
# Why these belong here, not in the system prompt:
# - Disciplines are about the PROJECT'S desired answer style (refuse vs guess;
#   cite verbatim vs paraphrase; "nicht spezifiziert" vs plausible filler).
#   That's tenant policy, not Brain mechanics.
# - Different projects need different disciplines (a brainstorming project
#   doesn't want refusal-on-empty-retrieval; a marketing project may not need
#   verbatim citations).
# - Owner-editable means the user can tune behavior per project without
#   touching code.
#
# Brain mechanics (the 3-step retrieval flow, `read_path` vs `read_path_original`,
# the binary→.md companion explanation, the KG hint block) STAY in the system
# prompt because they are infrastructure facts that don't change per project.
DEFAULT_PROJECT_INSTRUCTIONS = (
    "**QUERY DISCIPLINE — keep mempalace_query short and content-bearing**:\n"
    "Queries are matched by vector similarity. Long, verbose queries with "
    "filler tokens drag the embedding toward generic chunks and HIDE the "
    "documents that perfectly match on filename or topic. Use 2-4 "
    "content-bearing keywords — the actual subject of the question — and "
    "drop everything else. Do not write the user's full question into the "
    "query.\n"
    "DROP these filler/generic tokens: 'Regelung', 'Regelungen', 'Policy', "
    "'Richtlinie', 'Vorschrift', 'Verantwortliche', 'durchführen', "
    "'Tätigkeiten', 'Aufgaben', 'Beschreibung', 'Übersicht', 'Definition', "
    "'allgemein', 'bank', 'Unternehmen', 'IT-Policy', 'wie', 'was', 'welche'.\n"
    "KEEP the rare, specific subject keywords — these are what discriminates "
    "documents.\n"
    "Examples (user question → good query):\n"
    "  • 'Wie ist die Datensicherung und Archivierung geregelt?' → "
    "`Datensicherung Archivierung`\n"
    "  • 'Welche Tätigkeiten werden im IT-Morgencheck durchgeführt und von "
    "wem?' → `IT-Morgencheck` (and as a second try: `Morgencheck Prozess`)\n"
    "  • 'Wie werden TAMBAS-Daten gesichert?' → `TAMBAS Sicherung` (NOT "
    "'TAMBAS Datensicherung Backup Sicherung Kernbankensystem' — synonym "
    "stuffing dilutes the signal).\n"
    "If the first short query yields nothing matching, try a different "
    "rare keyword pair, NOT a longer version of the same query.\n"
    "\n"
    "**REFUSAL DISCIPLINE — read carefully**:\n"
    "If `mempalace_query` returns 0 relevant drawers (and after you've read "
    "the top drawers' source files in full and confirmed they don't contain "
    "the information), the project does NOT contain it. You MUST then answer:\n"
    "  'Diese Information ist im aktuellen Projektwissen nicht enthalten. "
    "Bitte fügen Sie das relevante Dokument zum Projekt hinzu oder "
    "konsultieren Sie eine andere Quelle.'\n"
    "Do NOT substitute general knowledge for indexed-document knowledge in "
    "project chats. Even if you know the topic well from training data — "
    "for compliance/policy/audit work, an answer that doesn't match an "
    "actual document on file is a compliance hazard. Refuse cleanly and "
    "say what's missing.\n"
    "Try at most 2-3 query rephrasings before refusing; do not spin on "
    "retrieval forever.\n"
    "\n"
    "**PRECISION DISCIPLINE — no plausible-sounding filler**:\n"
    "When the source does not give a concrete value (interval, frequency, "
    "threshold, count, deadline, length, duration), write `nicht "
    "spezifiziert` — never substitute a plausible default like "
    "'regelmäßig', 'häufig', 'sofort', 'kürzer', 'mindestens X Zeichen', "
    "'alle 12 Monate', 'mindestens jährlich'. If you use any qualifying "
    "adverb or comparative ('regelmäßig', 'häufiger', 'kürzer', 'sofort', "
    "'zeitnah', 'angemessen'), the very next characters must be a "
    "wörtliches Zitat (`> \"...\"`) from the read_document output proving "
    "the source actually says that. No quote → drop the qualifier. "
    "ISO-27001-typical phrasing from training data is NOT a source.\n"
    "\n"
    "**CITATION DISCIPLINE — per-claim, not per-block**:\n"
    "EVERY factual sentence and EVERY bullet point that came from the "
    "project must carry its OWN [Quelle: <basename> — \"<wörtliches Zitat "
    "10-25 Wörter>\"] reference right after the claim. One citation at "
    "the end of a 5-bullet list is INSUFFICIENT — the reader cannot tell "
    "which bullet came from which source, and bullets without an explicit "
    "citation are where paraphrase drift and fabrication slip in. Treat "
    "each bullet as an independent claim that must stand on its own with "
    "its own quote.\n"
    "If you cannot find a verbatim quote in the read_document output that "
    "supports a specific bullet — DELETE that bullet. Do not write claims "
    "you cannot cite. A shorter, fully-cited answer is always preferable "
    "to a longer answer with uncited bullets.\n"
    "The verbatim quote (10-25 words, copied EXACTLY from the read_document "
    "output) is mandatory — it lets the user search the original PDF with "
    "Cmd+F and verify the claim. Without a quote, the citation is "
    "unverifiable.\n"
    "Two correct examples:\n"
    "  • Single-sentence claim:\n"
    "    'Multilogin-Berechtigungen müssen vom Datenowner genehmigt werden "
    "[Quelle: 4_1_0_ARL_Systemberechtigungen.pdf — \"Berechtigungen können "
    "ferner angeben, ob es sich um eine Berechtigung handelt, wo die "
    "Zugangsdaten nicht einer einzelnen Person zugewiesen werden kann\"].'\n"
    "  • Bullet list (each bullet has its own quote):\n"
    "    - Multilogin = nicht zuordenbar [Quelle: "
    "4_1_0_ARL_Systemberechtigungen.pdf — \"Zugangsdaten nicht einer "
    "einzelnen Person zugewiesen werden\"].\n"
    "    - Genehmigung durch Datenowner [Quelle: "
    "4_1_0_ARL_Systemberechtigungen.pdf — \"Vor Veränderungen ist eine "
    "Zustimmung des jeweiligen Datenowners einzuholen\"].\n"
    "    - (kein dritter Bullet, weil keine weitere Aussage im "
    "read_document gefunden — lieber weglassen als raten.)\n"
    "Use the basename only (e.g. `4_0_0_ARL_IKT Strategie.pdf` — not the "
    "full path). **STRIP THE `.md` COMPANION SUFFIX**: when a drawer's "
    "`source_file` ends in `.brain-extracted/<name>.<ext>.md`, cite the "
    "ORIGINAL binary's name (e.g. `policy.pdf`, NOT `policy.pdf.md`). "
    "**DO NOT invent paragraph numbers like `§164` or `§3.2`** — the `.md` "
    "companions do NOT preserve the original document's paragraph "
    "numbering; any `§N` you write will be fabricated. Only add a locator "
    "if it is genuinely present in the read_document text: `Page N` for "
    "PDFs (markitdown marks page boundaries), `Slide N` for PPTX, "
    "`Sheet \"Name\"` for XLSX. If no clean locator exists, the verbatim "
    "quote alone is sufficient.\n"
    "Multiple sources for one claim → repeat the bracket: [Quelle: "
    "A.pdf — \"...\"] [Quelle: B.docx — \"...\"]."
)

CAVEMAN_CHAT_PROMPTS = {
    1: (  # lite
        "\n\nRESPONSE STYLE — LITE COMPRESSION: "
        "Remove filler words (just, really, basically, actually, honestly). "
        "Remove hedging (might, could, perhaps — be definitive). "
        "No pleasantries or greetings. Keep grammar intact. "
        "Code blocks, URLs, paths, commands unchanged.\n"
    ),
    2: (  # full
        "\n\nRESPONSE STYLE — CAVEMAN MODE: "
        "Terse like caveman. Technical substance exact. Only fluff die. "
        "Drop: articles, filler (just/really/basically), pleasantries, hedging. "
        "Fragments OK. Short synonyms. Code unchanged. "
        "Pattern: [thing] [action] [reason]. [next step]. ACTIVE EVERY RESPONSE.\n"
    ),
    3: (  # ultra
        "\n\nRESPONSE STYLE — ULTRA CAVEMAN: "
        "Max compression. Abbreviate everything. No articles, no filler, no hedging, "
        "no pleasantries, no complete sentences needed. Telegraphic style. "
        "Use symbols (→ = > &) over words. Lists over prose. "
        "Code/URLs/paths unchanged. ACTIVE EVERY RESPONSE.\n"
    ),
}

CAVEMAN_SYSTEM_PROMPTS = {
    1: (  # lite — strip verbose examples, collapse whitespace
        "SYSTEM PROMPT COMPRESSION ACTIVE (lite): The instructions below are authoritative "
        "but may be verbose. Ignore stylistic padding in them. Focus on substance.\n\n"
    ),
    2: (  # full — aggressive compression prefix
        "SYSTEM PROMPT COMPRESSION ACTIVE (full): Instructions below heavily compressed. "
        "Interpret telegraphically. Fragments = complete thoughts. "
        "Abbreviations, symbols, shorthand all valid.\n\n"
    ),
    3: (  # ultra — max compression prefix
        "SYSTEM PROMPT COMPRESSION ACTIVE (ultra): All instructions below max-compressed. "
        "Interpret minimal syntax. No prose expected in instructions. "
        "Symbol-heavy, list-only, zero redundancy.\n\n"
    ),
}


def _caveman_compress_text(text: str, level: int) -> str:
    """Apply rule-based compression to system prompt text based on caveman level."""
    import re
    if level <= 0:
        return text
    if level >= 1:
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'  +', ' ', text)
        text = re.sub(r'(?m)^[ \t]+', '', text)
    if level >= 2:
        for word in ['please ', 'Please ', 'kindly ', 'Kindly ', 'Note that ', 'note that ',
                      'It is important to ', 'it is important to ', 'Make sure to ', 'make sure to ',
                      'Be sure to ', 'be sure to ', 'Remember to ', 'remember to ']:
            text = text.replace(word, '')
        text = re.sub(r'\b(the|a|an|The|A|An) (?=[A-Z])', '', text)
        text = re.sub(r'(?m)^#+\s+', '', text)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'(?m)^---+$', '', text)
    if level >= 3:
        text = re.sub(r'\b(you should|you can|you may|You should|You can|You may)\b', '', text)
        text = re.sub(r'\b(For example|for example|e\.g\.|i\.e\.),?\s*[^.]*\.', '', text)
        text = re.sub(r'\b(that is|which is|this is|these are|there are)\b', '', text)
        text = re.sub(r'  +', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# --- Tool Definitions ---

TOOL_DEFINITIONS = [
    {
        "name": "read_file",
        "description": (
            "Read the contents of a file. Returns the full text content. "
            "Use offset and limit to read a specific range of lines from large files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or relative file path to read"},
                "offset": {"type": "integer", "description": "Line number to start reading from (1-based, default: 1)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to read (default: all)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or overwrite an existing file with the given content. "
            "Creates parent directories automatically if they don't exist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write to"},
                "content": {"type": "string", "description": "The full content to write to the file"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Edit an existing file by replacing an exact string match with new content. "
            "The old_string must match exactly (including whitespace/indentation). "
            "Use replace_all=true to replace every occurrence."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to edit"},
                "old_string": {"type": "string", "description": "Exact string to find and replace"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default: false)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_directory",
        "description": (
            "List files and directories at a given path. "
            "Supports glob patterns (e.g. '*.py', '**/*.js'). "
            "Returns file names, sizes, and types."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path to list (default: current directory)"},
                "pattern": {"type": "string", "description": "Glob pattern to filter results (e.g. '*.py', '**/*.ts')"},
                "recursive": {"type": "boolean", "description": "List recursively (default: false)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": [],
        },
    },
    {
        "name": "search_files",
        "description": (
            "Search for a regex pattern across files. Returns matching lines with file paths and line numbers. "
            "Similar to grep/ripgrep. Use glob to filter which files to search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regex pattern to search for"},
                "path": {"type": "string", "description": "Directory or file to search in (default: current directory)"},
                "glob": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py')"},
                "case_insensitive": {"type": "boolean", "description": "Case-insensitive search (default: false)"},
                "max_results": {"type": "integer", "description": "Maximum number of matches to return (default: 50)"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "execute_command",
        "description": (
            "Execute a shell command and return its output (stdout + stderr). "
            "Commands run in the current working directory with no TTY (non-interactive). "
            "IMPORTANT: Only use non-interactive commands. For example use 'top -l 1' (not 'top'), "
            "'ps aux' (not 'htop'), 'cat' (not 'less'). "
            "Use this for: running scripts, git commands, package managers, compiling, testing, "
            "system administration, or any shell operation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute"},
                "cwd": {"type": "string", "description": "Working directory for the command (default: current directory)"},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: 120)"},
                "node": {"type": "string", "description": "Remote node name or 'tag:NAME' to execute on a remote node instead of locally"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "python_exec",
        "description": (
            "Execute Python code in a sandboxed subprocess. "
            "Use for computation, data transformation, file processing, math, JSON/CSV parsing, "
            "or any task that benefits from writing code instead of chaining multiple tool calls. "
            "Standard library is fully available. Packages from the configured venv are available if set. "
            "The working directory is the session's artifact folder — any files you write there "
            "(e.g. open('results.txt','w')) become viewable artifacts for the user. "
            "For large results, WRITE them to a file instead of printing to stdout. "
            "Print only a short summary to stdout. Stdout is returned as the tool result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute. Use print() for output. Write large results to files instead of printing."},
                "timeout": {"type": "integer", "description": "Timeout in seconds (default: from config, typically 30)"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "web_fetch",
        "description": (
            "Fetch content from a URL. Returns the response body as text. "
            "Works with web pages, APIs, raw files, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "method": {"type": "string", "description": "HTTP method (default: GET)", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
                "headers": {"type": "object", "description": "Additional HTTP headers as key-value pairs"},
                "body": {"type": "string", "description": "Request body (for POST/PUT/PATCH)"},
                "max_length": {"type": "integer", "description": "Max response length in characters (default: 50000)"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "gmail_inbox",
        "description": "List recent emails from Gmail inbox. Returns subject, from, date for each email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of emails to return (default: 10)"},
                "folder": {"type": "string", "description": "Mailbox folder (default: INBOX)"},
            },
            "required": [],
        },
    },
    {
        "name": "gmail_read",
        "description": "Read a specific email by its ID. Returns full body, attachments list, headers.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Email ID from gmail_inbox or gmail_search"},
                "folder": {"type": "string", "description": "Mailbox folder (default: INBOX)"},
            },
            "required": ["id"],
        },
    },
    {
        "name": "gmail_search",
        "description": "Search emails using Gmail search syntax (from:, subject:, is:unread, after:, has:attachment, etc).",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Gmail search query"},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "gmail_send",
        "description": "Send an email via Gmail. Supports optional file attachments — pass relative paths (resolved against the current session's artifact folder, matching write_file) or absolute paths.",
        "input_schema": {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient email address. Comma/semicolon-separated or a list for multiple recipients."},
                "subject": {"type": "string", "description": "Email subject"},
                "body": {"type": "string", "description": "Email body (plain text)"},
                "cc": {"type": "string", "description": "CC email address (optional)"},
                "attachments": {
                    "type": "array",
                    "description": "Optional list of file paths to attach. Relative paths resolve against the current session's artifact folder.",
                    "items": {"type": "string"},
                },
            },
            "required": ["to", "subject", "body"],
        },
    },
    {
        "name": "gmail_reply",
        "description": "Reply to an existing email by its ID. Preserves threading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Email ID to reply to"},
                "body": {"type": "string", "description": "Reply body (plain text)"},
            },
            "required": ["id", "body"],
        },
    },
    {
        "name": "exa_search",
        "description": (
            "Search the web using Exa AI for current, relevant information. "
            "Use this tool whenever the user asks to search the web, look something up, "
            "find recent news, or get current information about any topic."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query or topic to look up",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of search results to return (default: 5)",
                    "minimum": 1,
                    "maximum": 20,
                },
                "category": {
                    "type": "string",
                    "description": "Optional category: news, research paper, tweet, company, people",
                    "enum": ["news", "research paper", "tweet", "company", "people"],
                },
            },
            "required": ["query"],
        },
    },
    # MemPalace migration: built-in memory_* tools unregistered from the
    # LLM-facing schema. Agents now query MemPalace directly via mempalace_query
    # below, which imports mempalace.searcher in-process (no MCP, no subprocess).
    # Mining is handled by background daemons in server.py; the user never runs
    # `mempalace mine` by hand.
    {
        "name": "mempalace_query",
        "description": (
            "Search long-term memory (MemPalace). Returns verbatim snippets "
            "(drawers) from past conversations, code, references, and "
            "attachments that match the query. Use this whenever the user "
            "asks about something they (or the agent) said before, a "
            "previously-mentioned project, a past decision, or code you've "
            "seen in this repo. Hybrid BM25+vector ranking; the daemon keeps "
            "the palace up to date automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for. Natural language or keywords.",
                },
                "wing": {
                    "type": "string",
                    "description": (
                        "Optional wing filter. Pass an agent id (e.g. 'main') to "
                        "search that agent's chat memories — auto-scoped to the "
                        "current user. Pass 'brain_code' for source/artifacts. "
                        "Omit to search all accessible wings."
                    ),
                },
                "room": {
                    "type": "string",
                    "description": (
                        "Optional room filter. Brain's project miner files all "
                        "policy/document content under room='general' and "
                        "auto-promoted artifacts under room='artifacts'. "
                        "Chat content (when chat-sync is on) uses 'chat', "
                        "'chat_summary', 'chat_attachment'. Web/search "
                        "references use 'reference'. **DO NOT GUESS room "
                        "names** — invented values like 'document' or "
                        "'documentation' return zero drawers and produce "
                        "false 'no information found' answers. Omit this "
                        "argument unless you have a verified room name "
                        "from a prior result."
                    ),
                },
                "n_results": {
                    "type": "integer",
                    "description": "Max drawers to return (default 5, max 25).",
                    "minimum": 1,
                    "maximum": 25,
                },
                "include_chat_history": {
                    "type": "boolean",
                    "description": (
                        "Project-pinned only. Default false. When true, search "
                        "the project's CHAT memory (past turns, summaries, "
                        "attachment metadata) instead of the project KNOWLEDGE "
                        "wing (mined documents + ingested files). Use when the "
                        "user asks 'what did we discuss earlier' / 'remember "
                        "when I said'. Outside a project this flag is ignored."
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "mempalace_kg_query",
        "description": (
            "Query the project's knowledge graph for an entity — get all "
            "(subject, predicate, object) triples where this entity appears. "
            "The graph is built by an LLM extractor over normative documents "
            "(policies, regulations, specs, contracts). Use this when the "
            "user asks 'what does X require / forbid / cite / define', 'who "
            "is responsible for X', 'what depends on X', or wants a "
            "structured view of obligations. Returns triples with source "
            "drawer references — use mempalace_query on the same source_file "
            "to read the verbatim chunk. Auto-scoped to the current project; "
            "refuses outside a project context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": (
                        "The entity to look up — verbatim in the document's "
                        "source language (e.g. German). Case-insensitive."
                    ),
                },
                "direction": {
                    "type": "string",
                    "enum": ["outgoing", "incoming", "both"],
                    "description": (
                        "outgoing = X→? (what this entity requires/forbids/"
                        "cites). incoming = ?→X (what depends on / refers to "
                        "this entity). both = union. Default outgoing."
                    ),
                },
                "as_of": {
                    "type": "string",
                    "description": (
                        "Optional date filter (ISO YYYY-MM-DD). Returns only "
                        "triples valid at that point in time. Omit for all "
                        "currently-valid triples."
                    ),
                },
            },
            "required": ["entity"],
        },
    },
    {
        "name": "mempalace_kg_search",
        "description": (
            "Find every triple matching a predicate filter (and optionally a "
            "subject or object substring). This is the contradiction- and "
            "coverage-detection primitive: 'show me every requires triple "
            "about retention', 'every cites triple referencing GDPR', 'every "
            "forbids rule applied to employees'. Compare the returned set to "
            "spot disagreements and gaps. Auto-scoped to the current project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "predicate": {
                    "type": "string",
                    "description": (
                        "Required. The relation type — must be lowercase "
                        "snake_case. Common: requires, forbids, permits, "
                        "defines, cites, applies_to, effective_from, "
                        "supersedes, responsible_party, condition, exception, "
                        "penalty."
                    ),
                },
                "subject_contains": {
                    "type": "string",
                    "description": (
                        "Optional substring filter on the subject (case-"
                        "insensitive). Use to narrow to a topic."
                    ),
                },
                "object_contains": {
                    "type": "string",
                    "description": (
                        "Optional substring filter on the object (case-"
                        "insensitive). Use to find e.g. all rules mentioning "
                        "'7 Jahre' or 'GDPR'."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": "Max triples to return (default 25, max 200).",
                    "minimum": 1,
                    "maximum": 200,
                },
            },
            "required": ["predicate"],
        },
    },
    {
        "name": "mempalace_kg_neighbors",
        "description": (
            "Multi-hop neighborhood traversal in the project's knowledge "
            "graph. Returns the entities reachable from a starting entity "
            "within N hops, plus the predicates connecting them. Use to "
            "answer 'what is everything connected to X' / 'what are the "
            "downstream implications of X' / 'which obligations cluster "
            "around the same topic'. Auto-scoped to the current project."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "The starting entity (verbatim, case-insensitive).",
                },
                "depth": {
                    "type": "integer",
                    "description": "Max hops (default 1, max 3).",
                    "minimum": 1,
                    "maximum": 3,
                },
                "predicate": {
                    "type": "string",
                    "description": (
                        "Optional: only follow edges with this predicate. "
                        "Useful for tracing a single relation type — e.g. "
                        "predicate='cites' walks the citation graph, "
                        "predicate='supersedes' walks version history."
                    ),
                },
            },
            "required": ["entity"],
        },
    },
    {
        "name": "save_chat_to_memory",
        "description": (
            "Enable saving this chat conversation to long-term memory (MemPalace). "
            "Use when the user says 'remember this', 'save this to memory', or wants "
            "to ensure the current conversation is persisted for future recall. "
            "Immediately syncs all messages in this chat to memory and enables "
            "automatic saving for any new messages in this session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "context_search",
        "description": "Search through compacted conversation history by keyword. Returns matching message excerpts from earlier in the conversation that have been summarized away.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword or phrase"},
                "limit": {"type": "integer", "description": "Max results (default: 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "context_detail",
        "description": "Expand a specific context summary to see the original messages it was created from. Use summary IDs from the conversation context header.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary_id": {"type": "string", "description": "The summary ID to expand"},
            },
            "required": ["summary_id"],
        },
    },
    {
        "name": "context_recall",
        "description": "Deep recall: search compacted conversation history and get a focused answer about a specific topic from earlier in the conversation. Uses a sub-LLM call to analyze original messages.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to recall from earlier conversation"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Format-aware document reader for PDF, DOCX, XLSX, PPTX, CSV/TSV, images, "
            "EML (email), MSG (Outlook), EPUB (ebook), and ZIP archives. "
            "Returns structured content: PDF pages, DOCX paragraphs/tables, XLSX sheets as markdown tables, "
            "PPTX slides with notes, CSV as markdown table, image metadata + vision description, "
            "EML headers+body, EPUB metadata+prose, ZIP recursive file listing with contents. "
            "For unknown extensions, falls back to plain text read."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to the document"},
                "sheet": {"type": "string", "description": "Sheet name for XLSX (default: all sheets)"},
                "pages": {"type": "string", "description": "Page range for PDF, e.g. '1-5' or '1,3,7'"},
                "slides": {"type": "string", "description": "Slide range for PPTX, e.g. '1-10' or '2,5'"},
                "include_tables": {"type": "boolean", "description": "PDF only: extract tables via pdfplumber and inline as markdown. Works well on PDFs with ruled cell borders (forms, financial reports, invoices). Turn OFF for academic papers, whitespace-aligned tables, or scanned PDFs — pdfplumber produces noisy output in those cases. Default false; adds ~1-3s per page."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_document",
        "description": (
            "Create a new document from markdown content. Dispatches by file extension: "
            ".docx (headings, tables, bold/italic), .xlsx (markdown tables to sheets), "
            ".pptx (# sections to slides), .pdf (basic formatted PDF via reportlab)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Output file path (extension determines format)"},
                "content": {"type": "string", "description": "Markdown content to convert into the document"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_document",
        "description": (
            "Targeted edits to existing documents. Actions by format: "
            "DOCX: replace_text (find/replace in paragraphs). "
            "XLSX: update_cell (sheet, cell, value), add_row (sheet, values). "
            "PPTX: update_slide (slide_index, title, body), add_slide (title, body)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to the document to edit"},
                "action": {
                    "type": "string",
                    "description": "Edit action to perform",
                    "enum": ["replace_text", "update_cell", "add_row", "update_slide", "add_slide"],
                },
                "params": {
                    "type": "object",
                    "description": (
                        "Action-specific parameters. "
                        "replace_text: {old_text, new_text}. "
                        "update_cell: {sheet, cell, value}. "
                        "add_row: {sheet, values (array)}. "
                        "update_slide: {slide_index (1-based), title, body}. "
                        "add_slide: {title, body}."
                    ),
                },
            },
            "required": ["path", "action", "params"],
        },
    },
    {
        "name": "delegate_task",
        "description": (
            "Delegate a task to another agent. Runs in a background thread with its own context. "
            "By default waits for result (wait=true). Set wait=false for async execution, "
            "then use task_status to poll for completion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "agent": {"type": "string", "description": "Target agent ID (e.g. 'research', 'health')"},
                "task": {"type": "string", "description": "Task description for the target agent"},
                "wait": {"type": "boolean", "description": "Wait for result (default: true). Set false for async."},
                "model": {"type": "string", "description": "Override model for this task (optional)"},
            },
            "required": ["agent", "task"],
        },
    },
    {
        "name": "task_status",
        "description": (
            "Check status of background tasks. Call with task_id to check a specific task, "
            "or without to list all tasks. Returns status (running/completed/cancelled/error) and result."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to check (optional, lists all if empty)"},
            },
            "required": [],
        },
    },
    {
        "name": "task_cancel",
        "description": "Cancel a running background task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to cancel"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "use_skill",
        "description": (
            "Load a skill's instructions into context. Skills provide specialized knowledge "
            "for specific tasks (e.g. github, docker, swift). Call this BEFORE performing a task "
            "that matches a skill. The skill's instructions will be returned as text — follow them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "skill": {"type": "string", "description": "Name of the skill to load"},
            },
            "required": ["skill"],
        },
    },
    {
        "name": "schedule_list",
        "description": "List all scheduled tasks with their status, next run time, and configuration.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "list_nodes",
        "description": (
            "List all registered remote nodes with their status, hostname, OS, tags, "
            "allowed tools, and resource usage. Use this to check what remote nodes are "
            "available before routing commands to them."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "schedule_history",
        "description": "Get execution history for scheduled tasks. Shows status, results, and timestamps.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filter by schedule name (optional)"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            "required": [],
        },
    },
    {
        "name": "mcp_connect",
        "description": (
            "Connect to an MCP server at runtime. Discovers tools from the server and makes them "
            "available as mcp_<name>_<tool> tools. Use transport='sse' for HTTP servers, 'stdio' for local commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "MCP server URL (for SSE) or command (for stdio)"},
                "name": {"type": "string", "description": "Friendly name for this connection"},
                "transport": {"type": "string", "description": "Transport type: 'sse' (default) or 'stdio'", "enum": ["sse", "stdio"]},
                "persist": {"type": "boolean", "description": "Save to mcp.json for reconnect on restart (default: false)"},
            },
            "required": ["url", "name"],
        },
    },
    {
        "name": "mcp_disconnect",
        "description": "Disconnect from a runtime MCP server. Its tools will no longer be available.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name of the MCP server to disconnect"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "mcp_servers",
        "description": "List all connected MCP servers with their tools and status.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "code_graph_build",
        "description": (
            "Build or rebuild the code structure graph for a directory. Parses source files "
            "using Tree-sitter AST parsing to extract functions, classes, imports, and call "
            "relationships. Supports Python, JavaScript, TypeScript, Go, Rust, Java, and more. "
            "Use incremental=true (default) to only re-parse changed files."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory to parse (absolute path)"},
                "incremental": {"type": "boolean", "description": "Only re-parse changed files (default: true)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "code_graph_query",
        "description": (
            "Query the code structure graph for structural relationships. Find callers/callees "
            "of a function, imports, inheritance, test coverage, and more. Build the graph first "
            "with code_graph_build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["callers_of", "callees_of", "imports_of", "importers_of",
                             "tests_for", "inheritors_of", "children_of", "file_summary"],
                    "description": "Type of structural query",
                },
                "target": {"type": "string", "description": "Qualified name or function/class name to query"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
            },
            "required": ["query_type", "target"],
        },
    },
    {
        "name": "code_graph_impact",
        "description": (
            "Blast-radius analysis: given a list of changed files, find all functions, classes, "
            "and files that could be affected. Uses BFS traversal of the code graph. "
            "Build the graph first with code_graph_build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of changed file paths",
                },
                "depth": {"type": "integer", "description": "Max traversal depth (default: 2)"},
            },
            "required": ["files"],
        },
    },
    {
        "name": "code_graph_enhance",
        "description": (
            "Enhance the code graph with LLM-generated summaries, architecture layer classification, "
            "and a guided tour. Actions: 'all' (default), 'summaries' (LLM descriptions per function/class), "
            "'layers' (classify as api/service/data/ui/util/test), 'tour' (dependency-ordered walkthrough)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["all", "summaries", "layers", "tour"],
                    "description": "What to generate (default: all)",
                },
                "batch_size": {"type": "integer", "description": "Max files to summarize per run (default: 20)"},
                "root_dir": {"type": "string", "description": "Root directory for tour (default: last build dir)"},
            },
            "required": [],
        },
    },
    {
        "name": "git_command",
        "description": (
            "Execute git operations with structured output. Actions:\n"
            "- status: working tree status (modified, staged, untracked files)\n"
            "- diff: show changes (optional file path, staged=true for staged only)\n"
            "- log: commit history (limit, author, since, path filters)\n"
            "- branch: list/create/switch branches (name, create=true, switch=true)\n"
            "- commit: create commit (message required, files=[] to stage specific files, all=true for -a)\n"
            "- stash: stash/pop/list (sub_action: save/pop/list/drop)\n"
            "- blame: annotate file lines (path, line_start, line_end)\n"
            "- show: show commit details (ref)\n"
            "- tag: list/create tags (name, message)\n"
            "- remote: list remotes or show remote info"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "diff", "log", "branch", "commit", "stash", "blame", "show", "tag", "remote"],
                    "description": "Git operation to perform",
                },
                "message": {"type": "string", "description": "Commit/tag message"},
                "files": {"type": "array", "items": {"type": "string"}, "description": "Specific files to stage/diff/blame"},
                "path": {"type": "string", "description": "File path for diff/blame/log"},
                "name": {"type": "string", "description": "Branch/tag name"},
                "ref": {"type": "string", "description": "Commit ref for show/diff (default: HEAD)"},
                "limit": {"type": "integer", "description": "Max entries for log (default: 20)"},
                "author": {"type": "string", "description": "Filter log by author"},
                "since": {"type": "string", "description": "Filter log since date (e.g., '1 week ago')"},
                "staged": {"type": "boolean", "description": "Show only staged changes for diff"},
                "create": {"type": "boolean", "description": "Create new branch/tag"},
                "switch": {"type": "boolean", "description": "Switch to branch"},
                "all": {"type": "boolean", "description": "Stage all changes for commit (-a)"},
                "sub_action": {"type": "string", "description": "Sub-action for stash (save/pop/list/drop)"},
                "line_start": {"type": "integer", "description": "Start line for blame"},
                "line_end": {"type": "integer", "description": "End line for blame"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "github_command",
        "description": (
            "Interact with GitHub via the gh CLI. Requires gh to be installed and authenticated. Actions:\n"
            "- pr_list: list open PRs (limit, state, author)\n"
            "- pr_create: create PR (title, body, base, head, draft)\n"
            "- pr_view: view PR details (number)\n"
            "- pr_merge: merge a PR (number, method=merge|squash|rebase)\n"
            "- pr_review: list PR reviews/comments (number)\n"
            "- issue_list: list issues (limit, state, labels)\n"
            "- issue_create: create issue (title, body, labels)\n"
            "- issue_view: view issue details (number)\n"
            "- repo_view: show repo info\n"
            "- release_list: list releases\n"
            "- workflow_list: list GitHub Actions workflows\n"
            "- workflow_run: view workflow run status (run_id)\n"
            "- api: raw GitHub API call (endpoint, method)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["pr_list", "pr_create", "pr_view", "pr_merge", "pr_review",
                             "issue_list", "issue_create", "issue_view",
                             "repo_view", "release_list", "workflow_list", "workflow_run", "api"],
                    "description": "GitHub operation to perform",
                },
                "number": {"type": "integer", "description": "PR or issue number"},
                "title": {"type": "string", "description": "PR/issue title"},
                "body": {"type": "string", "description": "PR/issue body"},
                "base": {"type": "string", "description": "Base branch for PR (default: main)"},
                "head": {"type": "string", "description": "Head branch for PR"},
                "draft": {"type": "boolean", "description": "Create PR as draft"},
                "method": {"type": "string", "description": "Merge method (merge/squash/rebase)"},
                "state": {"type": "string", "description": "Filter by state (open/closed/all)"},
                "labels": {"type": "string", "description": "Comma-separated labels"},
                "author": {"type": "string", "description": "Filter by author"},
                "limit": {"type": "integer", "description": "Max results (default: 20)"},
                "run_id": {"type": "string", "description": "Workflow run ID"},
                "endpoint": {"type": "string", "description": "API endpoint for raw call (e.g., repos/{owner}/{repo}/issues)"},
                "api_method": {"type": "string", "description": "HTTP method for API call (GET/POST/PATCH)"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "tool_search",
        "description": (
            "Search for available tools by name or description. Use this when you need a "
            "tool that isn't in your current tool list. Returns matching tool schemas that "
            "will be available on subsequent turns. Useful when MCP tools are deferred."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query to match against tool names and descriptions"},
                "max_results": {"type": "integer", "description": "Maximum results to return (default: 5)"},
            },
            "required": ["query"],
        },
    },
    # --- Worker Subagent Tools (v8.0.0) ---
    {
        "name": "get_artifact_detail",
        "description": (
            "Retrieve the raw content of a worker artifact. Use this to inspect "
            "the full output of a tool that was executed by a worker subagent. "
            "Optionally filter by a search query to extract only relevant sections."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string", "description": "The artifact filename (from the worker envelope's artifacts[].artifact_id)"},
                "query": {"type": "string", "description": "Optional search term to extract only matching lines with context"},
                "offset": {"type": "integer", "description": "Character offset to start reading from (default: 0)"},
                "limit": {"type": "integer", "description": "Maximum characters to return (default: 16384)"},
            },
            "required": ["artifact_id"],
        },
    },
    {
        "name": "worker_status",
        "description": "Get current state of running or completed worker subagents. Use this to inform the user what a background task is doing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Specific worker ID. Omit for all workers in this session."},
            },
        },
    },
    {
        "name": "worker_abort",
        "description": "Abort a running worker subagent. Idempotent — aborting an already-aborted worker returns success.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to abort"},
                "reason": {"type": "string", "description": "Reason for aborting (logged and shown to user)"},
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "worker_pause",
        "description": "Pause a running worker at its next safepoint without terminating it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to pause"},
                "reason": {"type": "string", "description": "Reason for pausing"},
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "worker_resume",
        "description": "Resume a paused worker without adding input.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to resume"},
            },
            "required": ["worker_id"],
        },
    },
    {
        "name": "worker_send",
        "description": "Send additional context or instructions to a running or paused worker. If paused, also resumes the worker.",
        "input_schema": {
            "type": "object",
            "properties": {
                "worker_id": {"type": "string", "description": "Worker ID to send to"},
                "message": {"type": "string", "description": "Message content to inject into the worker's context"},
                "role": {"type": "string", "enum": ["user", "system"], "description": "Message role (default: user)"},
            },
            "required": ["worker_id", "message"],
        },
    },
    {
        "name": "worker_ask_user",
        "description": (
            "Ask the user one or more questions that cannot be decided from available context. "
            "The worker will pause until answered. Only available inside a worker subagent. "
            "Use sparingly — prefer making reasonable decisions autonomously. "
            "When the user explicitly asks you to pose questions to them (e.g. \"ask me 5 questions\", "
            "\"interview me\", \"quiz me\"), pass them all in the `questions` array in a single call — "
            "this renders one interactive answer card in the UI with all questions at once. "
            "For a single clarifying question, either pass `question` (string) or a 1-item `questions` array."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "Batch of 1-8 questions to ask the user. Each item: {question: str, options?: [str]}. Use this to ask multiple questions in one UI card.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The question text"},
                            "options": {"type": "array", "items": {"type": "string"}, "description": "Optional multiple-choice options for this question"},
                        },
                        "required": ["question"],
                    },
                    "minItems": 1,
                    "maxItems": 8,
                },
                "question": {"type": "string", "description": "Single question text (alternative to `questions`). Use `questions` for multi-question batches."},
                "options": {"type": "array", "items": {"type": "string"}, "description": "Optional multiple-choice options (only used with single `question`)"},
                "context_summary": {"type": "string", "description": "Brief context so the user understands why these questions are being asked"},
                "timeout_seconds": {"type": "integer", "description": "Seconds to wait for an answer before aborting (default: 300)"},
            },
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user one or more clarifying questions that cannot be decided from available context. "
            "The chat pauses until the user answers. Use sparingly — prefer making reasonable decisions autonomously. "
            "When the user explicitly asks you to pose questions to them (e.g. \"ask me 5 questions about X\", "
            "\"interview me\", \"quiz me\"), pass them all in the `questions` array in a single call — "
            "this renders one interactive answer card in the UI with all questions at once. "
            "For a single clarifying question, either pass `question` (string) or a 1-item `questions` array. "
            "Returns {\"answers\": {<question>: <answer>, ...}} for a batch, or {\"answer\": str} for a single question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "description": "Batch of 1-8 questions to ask the user. Each item: {question: str, options?: [str]}. Use this to ask multiple questions in one UI card.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The question text"},
                            "options": {"type": "array", "items": {"type": "string"}, "description": "Optional multiple-choice options for this question"},
                        },
                        "required": ["question"],
                    },
                    "minItems": 1,
                    "maxItems": 8,
                },
                "question": {"type": "string", "description": "Single question text (alternative to `questions`). Use `questions` for multi-question batches."},
                "options": {"type": "array", "items": {"type": "string"}, "description": "Optional multiple-choice options (only used with single `question`)"},
                "context_summary": {"type": "string", "description": "Brief context so the user understands why these questions are being asked"},
                "timeout_seconds": {"type": "integer", "description": "Seconds to wait for an answer before aborting (default: 300)"},
            },
        },
    },
    {
        "name": "generate_image",
        "description": (
            "Generate an image from a text prompt using Mistral's image generation service. "
            "The generated image is saved to the session artifact folder and shown in the Artifacts panel. "
            "Use for marketing visuals, illustrations, diagrams, product mockups, or any creative imagery. "
            "Be descriptive: include subject, mood, style, lighting, and composition details for best results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the image to generate. Include subject, style, mood, lighting, composition.",
                },
                "aspect_ratio": {
                    "type": "string",
                    "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"],
                    "description": "Image aspect ratio. Use 16:9 for banners/landscapes, 9:16 for mobile/stories, 1:1 for square posts, 4:3 for classic format. Default: 1:1",
                },
                "style": {
                    "type": "string",
                    "description": "Optional style hint, e.g. 'photorealistic', 'flat illustration', 'minimalist', 'cinematic', 'watercolor'. Appended to the prompt.",
                },
            },
            "required": ["prompt"],
        },
    },
]

# Build OpenAI-compatible format automatically
TOOL_DEFINITIONS_OPENAI = []
for _td in TOOL_DEFINITIONS:
    TOOL_DEFINITIONS_OPENAI.append({
        "type": "function",
        "function": {
            "name": _td["name"],
            "description": _td["description"],
            "parameters": {
                "type": _td["input_schema"]["type"],
                "properties": _td["input_schema"]["properties"],
                "required": _td["input_schema"].get("required", []),
            },
        },
    })

# Tool name → definition index for fast lookup
_TOOL_DEF_INDEX = {td["name"]: td for td in TOOL_DEFINITIONS}
_TOOL_DEF_OPENAI_INDEX = {td["function"]["name"]: td for td in TOOL_DEFINITIONS_OPENAI}

# Tool groups for per-agent filtering (agents can specify groups or individual tool names)
TOOL_GROUPS = {
    "core": {"read_file", "write_file", "edit_file", "list_directory", "search_files",
             "execute_command", "tool_search", "ask_user"},
    "memory": {"mempalace_query", "save_chat_to_memory",
               "mempalace_kg_query", "mempalace_kg_search",
               "mempalace_kg_neighbors"},
    "context": {"context_search", "context_detail", "context_recall"},
    "web": {"web_fetch", "exa_search"},
    "email": {"gmail_inbox", "gmail_read", "gmail_search", "gmail_send", "gmail_reply"},
    "documents": {"read_document", "write_document", "edit_document"},
    "delegation": {"delegate_task", "task_status", "task_cancel"},
    "code_graph": {"code_graph_build", "code_graph_query", "code_graph_impact",
                   "code_graph_enhance"},
    "git": {"git_command", "github_command"},
    "scheduler": {"schedule_list", "schedule_history"},
    "mcp": {"mcp_connect", "mcp_disconnect", "mcp_servers"},
    "skills": {"use_skill"},
    "nodes": {"list_nodes"},
    "code_exec": {"python_exec"},
    "workers": {"get_artifact_detail", "worker_status", "worker_abort",
                "worker_pause", "worker_resume", "worker_send",
                "worker_ask_user"},
    "image_gen": {"generate_image"},
}

# Default tool groups included for all agents (if no explicit config)
DEFAULT_TOOL_GROUPS = {"core", "memory", "context", "web", "delegation", "git", "skills",
                       "nodes", "scheduler", "mcp", "workers"}


TOKEN_CONFIG_DEFAULTS = {
    "tool_groups": None,           # None = all tools, list = specific groups from TOOL_GROUPS
    "extra_tools": None,           # Additional individual tool names beyond groups
    "include_tools_guide": True,   # Inject tools.md into system prompt
    "include_memory_summary": False, # MemPalace migration: built-in memory summary disabled
    "memory_summary_cap": 3000,    # (unused after migration; agents query mempalace MCP directly)
    "compact_threshold": None,     # None = use default (0.60), float = override
    "scheduled_task_tools": True,  # Include full tool schema in scheduled tasks
    "mcp_tool_filter": None,       # None = all MCP tools; list of patterns (exact or fnmatch glob) to allow
    "mcp_tool_exclude": None,      # None = exclude nothing; list of patterns applied after filter
    "deferred_tool_groups": ["email", "documents", "code_graph", "scheduler"],  # Groups loaded on-demand via tool_search
}


# ─── Model optimization profiles ────────────────────────────────────────────
#
# A profile is a sparse overlay on a model's config. Fields only appear when
# the profile has an opinion — everything else falls through to the raw model
# config / defaults. Profile name lives on the model as `profile`; resolved
# lazily via resolve_model_settings(mid) so editing a profile definition
# updates every model using it without touching config.json.
#
# Precedence (lowest → highest):
#   defaults < model profile overlay < raw model fields < agent config < per-request
#
# Why these specific knobs:
#   - speed: optimises for KV-prefix stability + instant first token on local
#     models. Fat-but-stable prompt beats lean-but-shifting — deferred tool
#     groups change between requests as the model calls tool_search, which
#     invalidates the primed prefix. Local compute is "free" so we don't care
#     about extra tokens.
#   - balanced: current default behaviour. What everything shipped with.
#   - frugal: cloud-money-saver. Aggressive deferral, caveman system prompt,
#     tighter limits. Only safe on capable cloud models — smaller locals get
#     dumber under caveman.
#   - custom: no overlay applied. Backward compat for hand-tuned models.
MODEL_PROFILES = {
    "speed": {
        "model": {
            "warmup_mode": "full",
            "parallel_tool_calls": True,
            "caveman_system": 0,
        },
        "token_config": {
            "include_tools_guide": True,
            "deferred_tool_groups": [],      # no deferral → stable KV prefix
            "compact_threshold": 0.85,       # delay compaction → keep cache warm
        },
        "limits": {
            "max_tool_rounds": 15,
            "tool_result_char_limit": 60000,
            "tool_results_total_tokens": 80000,
            "context_safety_ratio": 0.95,
        },
    },
    "balanced": {
        "model": {
            "parallel_tool_calls": True,
            "caveman_system": 0,
        },
        "token_config": {
            "include_tools_guide": True,
            "deferred_tool_groups": ["email", "documents", "code_graph", "scheduler"],
            "compact_threshold": 0.70,
        },
        "limits": {
            "max_tool_rounds": 15,
            "tool_result_char_limit": 30000,
            "tool_results_total_tokens": 50000,
            "context_safety_ratio": 0.95,
        },
    },
    "frugal": {
        "model": {
            "warmup": False,
            "warmup_allow_cloud": False,
            "parallel_tool_calls": True,
            "caveman_system": 2,
        },
        "token_config": {
            "include_tools_guide": False,
            "deferred_tool_groups": ["email", "documents", "code_graph",
                                     "scheduler", "nodes", "git"],
            "compact_threshold": 0.50,
        },
        "limits": {
            "max_tool_rounds": 8,
            "tool_result_char_limit": 15000,
            "tool_results_total_tokens": 25000,
            "context_safety_ratio": 0.90,
        },
    },
}


def get_model_profile(mid: str) -> str:
    """Return the profile name for a model ('speed', 'balanced', 'frugal', 'custom').

    Models without an explicit `profile` field default to 'custom' (no overlay)
    for backward compat with hand-tuned configs.
    """
    cfg = (_models_config or {}).get(mid, {}) or {}
    p = cfg.get("profile")
    if p in MODEL_PROFILES or p == "custom":
        return p
    return "custom"


def resolve_model_settings(mid: str) -> dict:
    """Return the model config with profile overlay applied.

    Profile overlay sets *defaults* — explicit per-model fields win. This lets
    users flip a model onto a profile and still override individual knobs.
    Returns a fresh dict; safe to mutate.
    """
    raw = dict((_models_config or {}).get(mid, {}) or {})
    profile = raw.get("profile")
    if profile not in MODEL_PROFILES:
        return raw
    overlay = MODEL_PROFILES[profile].get("model", {})
    for k, v in overlay.items():
        if k not in raw or raw[k] is None:
            raw[k] = v
    return raw


def resolve_profile_token_config(mid: str) -> dict:
    """Return the profile's token_config fragment, or empty dict."""
    profile = get_model_profile(mid)
    if profile not in MODEL_PROFILES:
        return {}
    return dict(MODEL_PROFILES[profile].get("token_config", {}))


def resolve_profile_limits(mid: str) -> dict:
    """Return the profile's limits fragment, or empty dict."""
    profile = get_model_profile(mid)
    if profile not in MODEL_PROFILES:
        return {}
    return dict(MODEL_PROFILES[profile].get("limits", {}))


def _filter_mcp_tools(mcp_tools: list[dict], is_openai: bool = False) -> list[dict]:
    """Apply per-agent MCP tool allow/deny patterns from token_config.

    Patterns match the prefixed LLM-facing name (e.g., "mcp_mempalace_search").
    Supports fnmatch glob syntax: "mcp_mempalace_*", "mcp_mempalace_diary_*".
    `mcp_tool_filter`: if set, only matching tools are kept.
    `mcp_tool_exclude`: applied after filter; matching tools are dropped.
    """
    if not mcp_tools:
        return mcp_tools
    tcfg = _get_token_config()
    allow = tcfg.get("mcp_tool_filter")
    deny = tcfg.get("mcp_tool_exclude")
    if not allow and not deny:
        return mcp_tools
    import fnmatch

    def _name_of(t: dict) -> str:
        if is_openai:
            return (t.get("function", {}) or {}).get("name", "")
        return t.get("name", "")

    def _matches_any(name: str, patterns) -> bool:
        if not patterns:
            return False
        for p in patterns:
            if p == name or fnmatch.fnmatchcase(name, p):
                return True
        return False

    result = []
    for t in mcp_tools:
        n = _name_of(t)
        if allow and not _matches_any(n, allow):
            continue
        if deny and _matches_any(n, deny):
            continue
        result.append(t)
    return result


def _get_token_config(agent_id: str | None = None) -> dict:
    """Get token optimization config for an agent, merged with defaults.

    Precedence (lowest → highest):
      TOKEN_CONFIG_DEFAULTS < model profile overlay < agent token_config

    Model comes from _thread_local._current_model (set per request). Profile
    contributes deferred_tool_groups, compact_threshold, include_tools_guide.
    """
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    result = dict(TOKEN_CONFIG_DEFAULTS)

    # Overlay model profile's token_config fragment if a model is bound
    mid = getattr(_thread_local, '_current_model', None)
    if mid:
        prof = resolve_profile_token_config(mid)
        for k, v in prof.items():
            if k in result:
                result[k] = v

    if not agent:
        return result
    cfg = agent.config.get("token_config", {})
    if isinstance(cfg, dict):
        for k in TOKEN_CONFIG_DEFAULTS:
            if k in cfg:
                result[k] = cfg[k]
    return result


def _get_agent_tool_names(agent_id: str | None = None) -> set[str] | None:
    """Get the set of allowed tool names for an agent based on its config.
    Returns None if no filtering is configured (all tools allowed)."""
    tcfg = _get_token_config(agent_id)
    tool_groups = tcfg.get("tool_groups")
    extra_tools = tcfg.get("extra_tools")
    if not tool_groups and not extra_tools:
        return None  # No filtering configured — all tools
    names = set()
    if tool_groups:
        for g in tool_groups:
            names.update(TOOL_GROUPS.get(g, set()))
    if extra_tools:
        names.update(extra_tools)
    return names


def _should_defer_mcp(defer_setting, mcp_tools: list[dict], model: str,
                      is_openai: bool = False) -> bool:
    """Decide whether to defer MCP tool schemas.

    defer_setting: True (always defer), False (never), "auto" (defer when MCP tokens > 10% of context)
    """
    if defer_setting is True:
        return bool(mcp_tools)
    if defer_setting is False:
        return False
    # "auto" mode: defer when MCP tool schemas would exceed 10% of context window
    if not mcp_tools:
        return False
    mcp_schema_chars = sum(len(json.dumps(t)) for t in mcp_tools)
    mcp_schema_tokens = mcp_schema_chars // 4
    max_ctx = get_model_max_context(model)
    threshold = max_ctx * 0.10  # 10% of context window
    return mcp_schema_tokens > threshold


def get_tool_breakdown(agent_id: str | None = None) -> dict:
    """Measure tool-definition token cost by group and individual tool.

    Each tool row is decomposed into:
      name_tokens / desc_tokens / schema_tokens / total_tokens
    so callers can see WHY a tool is expensive (usually: bloated input_schema).

    Token estimation uses len(json.dumps(x)) // 4 — same method as the request-payload snapshot.
    """

    def _tok(obj) -> int:
        if obj is None:
            return 0
        if isinstance(obj, str):
            return len(obj) // 4
        try:
            return len(json.dumps(obj)) // 4
        except (TypeError, ValueError):
            return 0

    def _decompose(td: dict) -> dict:
        name = td.get("name") or (td.get("function", {}) or {}).get("name", "")
        desc = td.get("description") or (td.get("function", {}) or {}).get("description", "")
        if isinstance(desc, (list, tuple)):
            desc = " ".join(str(x) for x in desc)
        schema = td.get("input_schema")
        if schema is None:
            schema = (td.get("function", {}) or {}).get("parameters", {})
        name_tok = _tok(name)
        desc_tok = _tok(desc)
        schema_tok = _tok(schema)
        total_tok = _tok(td)
        # Track schema complexity for at-a-glance diagnosis
        props = {}
        required_count = 0
        if isinstance(schema, dict):
            props = schema.get("properties", {}) or {}
            required_count = len(schema.get("required", []) or [])
        return {
            "name": name,
            "desc": (desc[:200] + "...") if isinstance(desc, str) and len(desc) > 200 else desc,
            "name_tokens": name_tok,
            "desc_tokens": desc_tok,
            "schema_tokens": schema_tok,
            "tokens": total_tok,  # alias for sorting
            "total_tokens": total_tok,
            "param_count": len(props) if isinstance(props, dict) else 0,
            "required_count": required_count,
        }

    # Reverse index for built-in tools: tool name -> group name
    name_to_group: dict[str, str] = {}
    for gname, names in TOOL_GROUPS.items():
        for n in names:
            name_to_group[n] = gname

    groups: dict[str, dict] = {}

    def _bump(gname: str, source: str, tool_info: dict):
        key = f"{source}:{gname}"
        g = groups.setdefault(key, {
            "name": gname, "source": source,
            "tool_count": 0, "tokens": 0, "tools": [],
            "name_tokens": 0, "desc_tokens": 0, "schema_tokens": 0,
        })
        g["tool_count"] += 1
        g["tokens"] += tool_info["total_tokens"]
        g["name_tokens"] += tool_info["name_tokens"]
        g["desc_tokens"] += tool_info["desc_tokens"]
        g["schema_tokens"] += tool_info["schema_tokens"]
        g["tools"].append(tool_info)

    # --- Built-in tools ---
    for td in TOOL_DEFINITIONS:
        name = td.get("name", "")
        if not name:
            continue
        info = _decompose(td)
        gname = name_to_group.get(name, "other")
        _bump(gname, "builtin", info)

    # --- MCP tools (grouped by actual server, via authoritative _tool_to_server map) ---
    mcp_mgr = getattr(_thread_local, 'mcp_manager', None) or _mcp_manager
    mcp_tools_list: list[dict] = []
    tool_to_server: dict[str, str] = {}
    if mcp_mgr:
        try:
            mcp_tools_list = mcp_mgr.get_tool_definitions() or []
            mcp_tools_list = _filter_mcp_tools(mcp_tools_list, is_openai=False)
            # Access the manager's reverse map safely.
            tool_to_server = dict(getattr(mcp_mgr, '_tool_to_server', {}) or {})
        except Exception:
            mcp_tools_list = []
            tool_to_server = {}
    for td in mcp_tools_list:
        name = td.get("name", "")
        if not name:
            continue
        info = _decompose(td)
        server = tool_to_server.get(name)
        if not server:
            # Fallback: parse "mcp_<server>_<tool>" prefix (current MCPManager naming)
            if name.startswith("mcp_"):
                rest = name[4:]
                # Take everything up to the first underscore as server name.
                # This is approximate — server names containing underscores will land in "mcp".
                server = rest.split("_", 1)[0] if "_" in rest else rest
            else:
                server = "mcp"
        _bump(server or "mcp", "mcp", info)

    # Sort tools within each group by token cost descending
    group_list = []
    for g in groups.values():
        g["tools"].sort(key=lambda t: t["total_tokens"], reverse=True)
        group_list.append(g)
    group_list.sort(key=lambda g: g["tokens"], reverse=True)

    total_tokens = sum(g["tokens"] for g in group_list)
    total_count = sum(g["tool_count"] for g in group_list)

    # Built-in tool group deferral status
    tcfg = _get_token_config()
    deferred_builtin_set = set(tcfg.get("deferred_tool_groups") or [])
    for g in group_list:
        g["deferred"] = g["source"] == "builtin" and g["name"] in deferred_builtin_set
    deferred_builtin_tokens = sum(g["tokens"] for g in group_list if g.get("deferred"))

    # Deferral status: would MCP be auto-deferred for the agent's current model?
    agent = getattr(_thread_local, 'current_agent', None) or _current_agent
    model = (agent.config.get("model") if agent else "") or ""
    max_ctx = get_model_max_context(model) if model else 0
    mcp_tokens = sum(g["tokens"] for g in group_list if g["source"] == "mcp")
    threshold = int(max_ctx * 0.10) if max_ctx else 0
    deferred = bool(mcp_tools_list) and threshold > 0 and mcp_tokens > threshold

    return {
        "groups": group_list,
        "total_tokens": total_tokens,
        "total_count": total_count,
        "builtin_tokens": sum(g["tokens"] for g in group_list if g["source"] == "builtin"),
        "mcp_tokens": mcp_tokens,
        "model": model,
        "max_context": max_ctx,
        "deferred_builtin_groups": sorted(deferred_builtin_set),
        "deferred_builtin_tokens": deferred_builtin_tokens,
        "deferrable_mcp": {
            "deferred": deferred,
            "tokens_saved_if_deferred": mcp_tokens if deferred else 0,
            "threshold": threshold,
        },
    }


def _filter_tools(tool_list: list[dict], allowed: set[str] | None,
                  is_openai: bool = False) -> list[dict]:
    """Filter a tool definition list to only include allowed tools.
    Returns tools sorted by name for prompt cache stability."""
    if allowed is None:
        filtered = list(tool_list)
    elif is_openai:
        filtered = [t for t in tool_list if t["function"]["name"] in allowed]
    else:
        filtered = [t for t in tool_list if t["name"] in allowed]
    # Sort deterministically by tool name for Anthropic prompt cache stability.
    # Consistent ordering prevents cache misses when tool list is assembled in different order.
    if is_openai:
        filtered.sort(key=lambda t: t.get("function", {}).get("name", ""))
    else:
        filtered.sort(key=lambda t: t.get("name", ""))
    return filtered


# --- Tool Execution ---

def _ok(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False)


def _err(msg: str) -> str:
    return json.dumps({"error": msg}, ensure_ascii=False)


def _route_to_node(tool_name: str, args: dict) -> str | None:
    """If args contain 'node', route to remote node via server API. Returns result string or None for local."""
    node = args.pop("node", None)
    if not node:
        return None
    try:
        import urllib.request
        body = json.dumps({"node": node, "tool": tool_name, "params": args}).encode("utf-8")
        req = urllib.request.Request(
            "http://127.0.0.1:8420/v1/nodes/execute",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        if "error" in result:
            return _err(f"Node '{node}': {result['error']}")
        return _ok(result)
    except Exception as e:
        return _err(f"Node routing error: {e}")


