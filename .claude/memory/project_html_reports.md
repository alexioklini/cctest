---
name: project_html_reports
description: "v9.211.0 — Studio + Deep Research reports now also render as editorial HTML (Odysseus visual_report look) via engine/report_html.py; .md stays canonical, .html is primary view/download; category templating in deep research"
metadata: 
  node_type: memory
  type: project
  originSessionId: ca1df871-4888-4386-8355-b7af3514cf2e
---

Built 2026-06-26 (v9.211.0). User wanted to mimic Odysseus's deep-research HTML reports
(github pewdiepie-archdaemon/odysseus, src/visual_report.py — AGPL, look reimplemented not
copied) as a high-quality alternative to our markdown Studio/Deep-Research output. User's
priorities (stated mid-build): "high quality and very good looking reports most important,
quality was already very good" + "mimic the odysseus html deep research reports". Goal:
HTML becomes the standard report format.

WHAT SHIPPED (asks #1+#2 of the odysseus three; #3 goal-based extraction SKIPPED by user):
- **engine/report_html.py** (NEW) — `render_report_html(markdown, title, meta, sources,
  category, stats)`. ZERO new deps (main server = bare /opt/homebrew/bin/python3, NO
  markdown/nh3/bleach — bs4 IS available). Carries a focused pure-Python markdown→HTML
  converter for our report subset (##/### headings, **bold**/*italic*/`code`, [links](url),
  bullet+numbered lists, > blockquotes, --- rules, pipe tables, fenced code). SAFE: escapes
  every text node BEFORE emitting tags, raw HTML never passes → no script/onclick survives.
  STYLE faithfully ported: cream/terracotta/gold palette, Charter serif display + system
  body, animated aurora gradient + SVG film-grain, drop cap, border-image gradient h2 rules,
  gold italic blockquote, sticky TOC sidebar w/ scrollspy, sources panel w/ domain labels.
  Light/dark via prefers-color-scheme, print-ready. Only JS = my own scoped scrollspy+copy
  IIFE (1 <script>).
- **engine/output_gen.py save_report_output** — THE shared seam (Studio presets AND Deep
  Research both route through it). Now writes BOTH .md (canonical: wiki/search/audio/editor
  all read it) + .html (primary downloadable). Best-effort: HTML render failure never blocks
  the .md save. New args category/sources/stats only style the HTML (Studio passes None).
- **#1 category templating** in engine/deep_research.py: `_classify_category` (cheap bg call,
  ~12 tok) → product/comparison/howto/factcheck/report; `_CATEGORY_OVERRIDES` (German format
  prompts) appended to `_synthesize` system prompt. Deep research passes category+sources+stats
  to save_report_output.
- **project_outputs.html_artifact_id** column (migration in server_lib/db.py ~line 919area) +
  whitelisted in update_project_output + serialized in handlers/projects.py _output_to_dict.
- **web/js/panels_studio.js studioOpenOutput**: output w/ html_artifact_id → fetch HTML via
  authenticated getArtifactContent (JSON {content}) → render into sandboxed iframe srcdoc
  (auth-free, isolated). .md is fallback. Download menu prefers html_artifact_id.

GOTCHAS / decisions:
- iframe MUST use srcdoc (not src=download-url): iframes can't send Bearer auth; getArtifactContent
  returns the HTML string as authenticated JSON.
- artifact-content endpoint already serves text/html for .html ext; _ARTIFACT_TYPE_MAP has "html".
- Verified: renderer end-to-end (Playwright light+dark screenshots), py_compile all 5 modules,
  brain.py CHANGELOG compile-checked ([[feedback_compile_check_brain_py]]), JS gate green
  (0 eslint errors, net-globals unchanged, smoke passed), live server 9.211.0 + migration col 22
  present, graceful SIGTERM restart ([[feedback_never_sigkill_brain]]).
- Skill (06-user-manual.md Studio+Deep sections) + curated changelog + both versions bumped.

NOT committed yet (user hasn't asked; [[feedback_commit_to_main]] = commit direct to main when asked).
OPEN/future: #3 goal-based extraction deferred.

=== FOLLOW-UP v9.212.0 (same day): Deep Research in NORMAL chat ===
User wanted Deep Research available in normal chat (not just project Studio), as a composer toggle.
DECISIONS (AskUserQuestion): result = SESSION ARTIFACT (.html+.md, card in chat); INDEPENDENT toggle
(not exclusive to web/thinking); button GRAYED OUT + tooltip when no search backend. USER FEEDBACK
mid-build: "keine emoji buttons - svg!!!" → button is microscope SVG, removed 🔬 emoji from card heading.
- engine/deep_research.py: NEW run_research_chat(*, agent_id, session_id, topic, user_id, budget,
  progress, cancelled) — SYNCHRONOUS on chat worker thread, no research_runs row, no project dedup,
  no propose-sources. Reuses leaf helpers (project_name='' → user wing). effective_chat_budget() shares
  budget resolution. Project _run_research UNTOUCHED (no refactor).
- handlers/chat.py: run_session_turn gains deep_research=False; branches at the run_turn call site →
  _run_deep_research_turn returns {reply:<card>} shaped like run_turn so all persistence/done logic
  reused. Progress via tool_progress SSE channel (phases Planen/Suchen/Lesen/Bericht schreiben). Cancel
  via session.cancel_token.cancelled (it's a PROPERTY not is_cancelled()).
- POST /v1/chat reads body.deep_research. NEW GET /v1/research/backend (any authed user, NOT admin) →
  {backend, available} for the UI gate (handler in handlers/admin_config.py, route in server.py).
- Frontend: btn-deep-research SVG in index.html; toggleDeepResearch()/refreshDeepResearchButton() +
  _deepResearchBackendAvailable() (memoised) in init.js; API.streamChat sets body.deep_research from
  state.activeChat.deepResearch; repaint in updateStatusBar + nav.js.
- CRITICAL BUG fixed during build: _get_artifact_session_folder(sid) returns only the FOLDER NAME
  (<date>_<sid>), NOT a path. Must wrap: os.path.join(_brain.AGENTS_DIR, agent_id, "artifacts", folder).
  Otherwise files write to CWD + _is_artifact_path() fails + artifact never registers. (file_tools.py:3782
  is the canonical resolver.)
- Verified: py_compile all; JS gate green (net-globals 1498→1502, +4 globals, baseline bumped to 1502);
  server 9.212.0 boots clean; /v1/research/backend authed returns {searxng, available:true} (live config
  HAS searxng — standalone import probe wrongly said no, [[feedback_never_probe_server_config_via_import]]);
  live end-to-end chat turn: 8 subqs→39 candidates→fetched 26→kept 22, progress SSE flowing.
- Skill 06-user-manual (composer toggle list) + SKILL.md version + curated changelog updated; brain.py
  CHANGELOG compile-checked.

=== FOLLOW-UP v9.212.1/.2 (same day): 6 UX gaps + model precedence, all from user feedback ===
v9.212.1 fixed 6 reported UX gaps in chat Deep Research:
1. Anfrage-N pill MICROSCOPE icon: msg_metadata['deep_research']=True + done_data['deep_research']=True;
   sessions.js meta.deep_research→msg._deepResearch; chat_send.js done.deep_research; chat_render.js scans
   turn memberIdxs for an assistant msg with _deepResearch → adds .turn-dr-icon SVG (terracotta) to the pill.
2. LIVE progress in chat stream: emit a SYNTHETIC tool_call (name=deep_research, tool_use_id=dr-<hex>) at
   start, stream phases as tool_progress with SAME tool_use_id (fields phase+note), close with tool_result.
   CLIENT FIELD NAMES MATTER: tool_call wants name/args, tool_result wants name/result (NOT tool_name/
   tool_input/content). Synthetic card is live-only (not persisted to metadata.tools), so reload shows
   pill+badges+card but not the live progress card — acceptable.
3. File badges on the answer: _run_deep_research_turn installs an event_callback into the WORKER's request
   context around the _after_file_write calls (worker thread does NOT set ctx.event_callback itself — only
   the dispatch thread does). Callback appends artifact_updated data → _dr_files → worker folds into
   created_files → msg_metadata['files'] → chat_render file badges (clickable → artifact panel).
4. ~30s panel delay = real synthesis time (files written AFTER synthesis); now visible via #2 progress.
5. Cost+context in status bar: worker folds _dr_meta tokens into _usage_totals + sets cost_logged=True;
   COST FIX = pass the chat sid as background_call(session_id=sid) via accumulator['cost_session_id'] so
   get_session_cost(sid) finds the rows (was keyed to research-<run_id> → status bar showed $0).
6. Context inclusion: run_research_chat gains extra_context + history_summary; _build_deep_research_context
   gathers attachment text (extract_attachment_text, cap 8) + fresh Websuche-basket content (_build_web_sources)
   + last-6-turns note → injected as HIGH-priority pseudo-source in synthesis.
v9.212.2: model precedence (user q: should use composer model). run_research_chat gains preferred_model;
precedence = chat-selected (session.model — already auto-router-resolved by the time the DR branch runs at
chat.py:4496) → deep_research_model knob → background default. Project _run_research unchanged.
- All VERIFIED live end-to-end (multiple real chat-API turns): deep_research flag, files(2, output, artifact_id),
  cost (e.g. 0.046, MATCHES persisted metadata), tokens, synthetic tool_call+57 progress+tool_result+2 artifact_updated.
  JS gate green (net-globals 1502 unchanged; smoke flaky-login but passes on retry). brain.py compile-checked each time.
v9.211.0+9.212.x COMMITTED to main (commit 4282e8be).

=== FOLLOW-UP v9.213.0 (same day): graphics + project knowledge + warning + new-chat reset ===
Four more user-requested items on chat Deep Research:
1. GRAPHICS in HTML reports (user: "grafiken, bilder, diagramme wo sinnvoll", chose all 3):
   - _GRAPHICS_INSTRUCTIONS added to _synthesize system prompt → model emits ```mermaid (process/timeline/
     hierarchy) + ```chart (JSON {type:bar|line|pie,title,data:[{label,value}]} for REAL numbers only) +
     ![](https) for real source images. "where it genuinely clarifies, never decorative, never fabricate".
   - report_html.py: fenced-block lang detection → _render_mermaid_inline (reuses image_gen.render_mermaid_file
     mmdc→SVG, _strip_svg_for_inline drops prolog + forces width:100%) + _render_chart_inline. New _IMAGE regex
     runs BEFORE _LINK (![..](..) else caught by link parser); https-only, referrerpolicy=no-referrer.
   - NEW engine/report_charts.py: dependency-free SVG bar/line/pie generator, report palette, all values
     html-escaped, returns None on bad JSON → caller falls back to raw block.
   - HERO image: _fetch_og_image() in deep_research.py reads og:image/twitter:image of top source via urllib
     (200KB cap, https-only); render_report_html(hero_image=) shows it full-width under headline.
   - CSS: .report-figure/.report-diagram/.report-chart/.hero-image in report_html _CSS.
2. PROJECT MemPalace/KG (user: project-chat DR must include mempalace/KG like normal chat): run_research_chat
   gains project_name; when set, output_gen._gather_sources (project-scoped mempalace retrieval) → high-priority
   "Projektwissen" block in extra_context, AND the 4 LLM helpers get project_name (wing scope not user wing).
   _run_deep_research_turn reads engine.get_request_context().project. Attachments+history stay project-independent.
3. WEB-LOCKOUT WARNING (user: warn if DR in project chat w/ web suppressed): when web_locked
   (=Websuche basket active + allow_further_web off), a ⚠️ banner LEADS the card ("DR nur mit Websuche voll
   sinnvoll — Weitere Websuche erlauben einschalten"); report still produced (basket+project knowledge as basis).
4. NEW-CHAT RESET (user: new chat project/projectless always DR off): newChat() + openSession() both set
   chat.deepResearch=false + repaint refreshDeepResearchButton() — it's an EPHEMERAL per-turn intent, NOT a saved
   session setting (mirrors why caveman/webBasket are reset; [[feedback_composer_controls_are_source_of_truth]]).
   newChat reset was MISSING (deepResearch not in the reset list) → it stuck across chats — the reported bug.
- VERIFIED: py_compile all; renderer end-to-end (mermaid flowchart + bar chart + image figure, Playwright screenshot);
  JS gate green (net-globals 1502, smoke passes). Live test on 9.213.0 in progress at memory-write time.
OPEN/future: could make HTML the format for chat-level summaries too. v9.213.0 NOT yet committed (await user).
