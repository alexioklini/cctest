# Agent Tool Reference

Every tool the LLM can call in a chat turn. Names match the actual
`tool_use` block. Dispatch path: sidecar emits `tool_use` → POSTs
`/v1/tools/call` to Brain → `server_lib/tool_mcp.handle_tools_call`
dispatches via `engine.TOOL_DISPATCH` (or MCP fallback) → result returned.

Tools are gated per-call by a 3-layer resolver, and the status is now
settable **per use-case** (purpose):
1. Global status + purposes (admin-edited `config.json → tool_settings`).
   Each tool has ONE scalar status, `state ∈ {active, inactive, deferred}` —
   active = in prompt · inactive = off · deferred = tool_search-only.
   The scalar `state` is the **catch-all default for every purpose**.
   An optional `states: {<purpose>: state}` map sets the status independently
   per channel; any purpose NOT in the map inherits the scalar default.
2. Per-agent override (`agent.json → token_config.tool_overrides.<name>`):
   `{states: {<purpose>: state}}` (or a legacy scalar `{state}`). A purpose
   entry here REPLACES the global state for that purpose; absent = inherit.
3. Call purpose / use-case: `interactive | transform | memory_summary |
   research_minimal | helpdesk`.

Per-purpose resolution is `resolve_tool_state_for(name, agent_id, purpose)`
(agent purpose → agent scalar → global purpose → global scalar → 'active').
`resolve_active_tools` applies it uniformly across ALL purposes: the per-purpose
base sets (interactive = agent's allowed set; memory_summary / research_minimal /
helpdesk = their fixed sets) are now **defaults** — a tool set `active`/`deferred`
for a purpose it isn't in is ADDED; one set `inactive` is REMOVED. This is
guarded by a no-op fast path: when no tool carries a `states.<purpose>` entry,
the channel's surface is byte-identical to before (preserving the warm-pool KV
prefix for `interactive`).

**Two editing surfaces.** General Settings → Tools shows a GLOBAL matrix: every
tool row carries a status dropdown per use-case (Chat · Transform · Memory ·
Research · Brainy) plus a per-channel status summary (active/inactive/deferred
counts + realized token size of the tool injection). The expanded tool panel's
single "Standard (alle Zwecke)" dropdown edits the scalar default. Agent
Settings → Tokens shows the per-agent override matrix (currently the Chat /
`interactive` column only — the resolver supports the rest, the UI exposes one).

Brainy (the helpdesk bot) runs with `purpose='helpdesk'` and a fixed read-only
tool set BY DEFAULT — see "Helpdesk tools" below. That set is now a default an
admin can extend/restrict via the helpdesk column: adding a write/exec tool there
makes Brainy able to write/run (the global matrix warns ⚠ on this). Since 9.22.0
the resolved tool names are enforced at dispatch: `tool_mcp` rejects any
`tool_use` not in the turn's allowed list before it runs. That allowed list is
the DISPATCHABLE set = **active ∪ deferred** (NOT in-prompt only) — so a deferred
tool the model reaches for (directly or after `tool_search`) RUNS; only hard-
EXCLUDED tools (Websuche web-lockout, helpdesk read-only) are rejected. (Before
9.131.0 the whitelist was in-prompt-only, so a deferred tool was wrongly rejected
'not available in this context' — deferred collapsed to disabled, e.g. read_document
on an attachment turn that the classifier had deferred; chat f2168652.)

Deferred tools are hidden from the initial list, dispatchable, and surfaced via
`tool_search`. Disabled tools are neither in-prompt, tool_search-able, nor
dispatchable (never in the base set).

**Two layers reach the model: the wire schema and the admin prose overlay.**
Each tool's wire schema (the `description` + `input_schema` on the `tools` array)
defaults to code (`engine/tool_schemas.py → TOOL_DEFINITIONS`).
- **Wire description — now editable** (v9.101.4): `config.json →
  tool_settings.<tool>.wire_description` overrides the code description. When set,
  `_filter_tools` (the single seam every purpose + warmup path uses) swaps it onto
  the wire dict the model receives (shallow-copying only overridden tools, so
  TOOL_DEFINITIONS is never mutated and non-overridden tools stay KV-stable).
  Empty = code default. Edited in the per-tool ⚙ config modal ("Beschreibung
  (Wire — editierbar)" + reset-to-default). GET /v1/tools/settings exposes
  `wire_description_code` / `wire_description_override` / effective
  `wire_description`.
- **`input_schema` stays read-only** — bound to the tool's Python signature; the
  modal shows it (param table + raw JSON) for verification only.
- **Admin "Prompt-Text" overlay** (description / when_to_use / warnings / examples
  in tool_settings) is SEPARATE from the wire schema: rendered as a `## <tool>`
  block appended to the system prompt by `_render_tool_descriptions` (gated by
  `applies_with`) — additional guidance layered on top, not the wire schema.

Editing the wire description (like a prose edit) changes the system-prompt tool
array, so the warm-pool KV prefix desyncs until the next warmup rebuild (no
explicit invalidation is wired — a one-off latency cost on the first turn after).

## Core file ops

- `read_file(path, start_line?, end_line?)` — read text file, optional line range
- `write_file(path, content, mode?)` — create/overwrite; relative paths land
  in the session's artifact folder. Writes are HARD-RESTRICTED to that folder:
  an absolute path or a relative `..` escape that resolves outside it is REFUSED
  with an error (v9.153.0). Same restriction on `write_document`. (CLI/warmup
  with no session: unrestricted fallback.)
- `edit_file(path, old_string, new_string, replace_all?)` — exact-string edit
- `list_directory(path, recursive?)` — ls
- `search_files(root, pattern, ...)` — grep / find
- `execute_command(cmd, cwd?, timeout?)` — shell. NO TTY, no stdin,
  `TERM=dumb`. Banned commands (sudo, rm -rf /, …) rejected.

## Document ops (binary-friendly)

- `read_document(path, ...)` — auto-routes by extension: PDF→markdown
  (pymupdf4llm default), docx/pptx, xlsx/**xlsm**/xls/**xlsb** (every sheet as a
  markdown table; `sheet=` selects one; **VBA macro source** is appended as
  ```vba blocks — never executed), csv/tsv, eml/msg, epub/zip, images. Honors the
  `.md` companion in `<dir>/.brain-extracted/<name>.<ext>.md`. Returns content
  VERBATIM (no size cap — only the model context limits a big read). Use this for
  any non-`.txt` attachment.
- `write_document(path, content, format, style?)` — produce docx/pdf/pptx/xlsx
  from markdown; embeds `![alt](file)` images (docx/pptx/pdf) → pair with
  render_diagram for reports/slides with diagrams. Formats: **.docx/.xlsx/.pptx/
  .pdf/.html** (html = self-contained styled web report, images inlined as
  base64 — ALWAYS use write_document, not write_file, for HTML reports so the
  preset is applied). `style=<preset>` applies an editable style (fonts/colors/
  layout + running header/footer/logo) from `agents/<agent>/skills/doc-styles/
  <preset>.yaml` (e.g. `corporate`) — deterministic, model just writes markdown.
  A DEFAULT preset applies even when style= is omitted (project `doc_style` →
  config `doc_styles.default` → `corporate` → built-in), so output is on-brand by
  default (v9.154.0).
  Header/footer text supports `{page}`/`{date}` tokens; the logo + footer render
  on docx/pdf pages, pptx slides, and the html header/footer bands.
- `edit_document(path, ...)` — structural edit

## Memory (MemPalace, direct — not MCP)

- `mempalace_query(query, wing?, room?, limit?)` — semantic search.
  In a project chat, force-scoped to `project__<id>`.
  - File-backed drawers return the matched chunk widened to its neighbours
    (prev+match+next, ~2–2.5 KB) inline + `content_via:"snippet+optional_read"`.
    `read_document(read_path)` when EITHER you need an exact quote/figure/table,
    OR the answer isn't fully in the window (cut off / detail continues beyond
    it); if the window answers the question, answer from it — don't read just to
    be thorough. Drawers with no file behind them (chat/profile/artifact) return
    their full verbatim text inline + `content_via:"snippet"`. (History:
    v9.34.0 BLANKED the snippet to force reads; v9.37.0 brought it back, widened
    + read-optional, to cut token cost — same trade-off as the KG span above.)
  - **Matched-regions auto-read** (v9.39.0): mempalace_query records which
    chunk_indices of each file matched this session; a follow-up
    `read_document(read_path)` on that `.md` returns ONLY the matched regions
    (union of ±2-chunk windows around each matched chunk), `format:"text-regions"`,
    not the whole file — files often match on scattered chunks, so this gets
    every relevant region at a fraction of the bytes. Automatic (no flag). Falls
    back to a full read when offset/limit is given, the file wasn't a query hit,
    or the regions cover ~the whole file. Eval: read bytes -71% (461->130 KB) at
    a measured -0.07 mean quality cost (occasionally clips needed context).
    Smart gates (v9.40.0): returns the WHOLE file (no trim) when the file is
    small (≤8 chunks / ≤6 KB) OR when the matched regions would add up to ≥75%
    of the file anyway (many scattered small matches negate the saving) —
    trims only when a large file has genuinely sparse matches.
  - **Cross-encoder reranker** (v9.38.0, `config.json → mempalace.reranker`,
    default ON): after vector retrieval, a BAAI/bge-reranker-v2-m3 cross-encoder
    re-ranks the top `top_k_in` (40) candidates by joint (query,passage) scoring;
    `matched_via` gains `+rerank`. Skipped when the top hit has a strong filename
    boost (≥0.20). Eval lifted wrong-doc-choice cases (C3/P2/C2) but slightly hurt
    out-of-corpus refusal (surfaces plausible-but-irrelevant passages).
- `mempalace_kg_query(...)` — entity/predicate filter on the KG
- `mempalace_kg_search(query)` — semantic KG search
- `mempalace_kg_neighbors(entity, depth?)` — entity neighborhood
  - All three KG tools return triples (subject/predicate/object + source_file
    + confidence) **plus a short verbatim `span`** (≤400 chars, capped) quoting
    the source when available. The span quotes a short fact directly;
    `read_document(source_file)` when you need surrounding context / an exact
    figure / text beyond the span, OR when the span doesn't itself contain what
    the question asks. (History: v9.36.0 STRIPPED the span
    to force reads after the eval P2/C2 wrong-document failures; v9.37.0 brought
    it back — capped + read-optional — because forcing a full read on every hit
    blew up token cost. Trade-off: span reopens some mis-cite risk, mitigated by
    the cap + a hint warning not to answer from a span that doesn't support the
    claim.)
- `save_chat_to_memory()` — flip current chat's `save_to_memory` to ON

(`mempalace_get_drawer`, `mempalace_list_drawers` are admin-side; see
`03-storage.md` for direct SQLite if you need to inspect MemPalace.)

### Wiki tools (the agent's long-term memory = the user-visible LLM Wiki)

As of v9.103.0 the wiki IS the agent's memory: a user-visible, editable page
tree, every saved page mirrored into MemPalace for search. These REPLACED the
old `memory_store`/`memory_recall`/`memory_delete`/`memory_shared` tools (gone).
In the `wiki` group. Scope `user` (private) | `team` (shared with the team) |
`global` (everyone). Access is enforced; pages nest via `parent_id`.

- `wiki_write(title, content?, page_id?, scope?, parent_id?, project?)` — create
  a page (give `title`) or update one (give `page_id`). Write durable facts/
  notes/summaries here. A human/agent edit makes a new version; only the current
  version is searchable.
- `wiki_read(query?, page_id?, filter?, limit?)` — `page_id` reads one full page;
  `query` searches the wiki semantically across ALL accessible wings (user +
  teams + global); neither lists the tree (`filter`: mine|team|global|all).
- `wiki_delete(page_id)` — delete a page (children re-parent to its parent).
- `wiki_structure(action?, filter?, page_id?, parent_id?, position?)` — `list`
  the accessible tree (default) or `move` a page (re-parent/reposition).

See `01-api.md` (LLM Wiki endpoints) + `03-storage.md` (wiki_pages schema). The
old MemoryStore .md-file backend is retired; the per-page history, promote, and
auto-feed-from-chat behavior live in the wiki, not a key/value store.

## Context manager

- `context_search(query)` — search the LCM DAG
- `context_detail(node_id)` — one node's content + lineage
- `context_recall(query)` — natural-language recall

## Web / email

- `web_fetch(url)` — GET one URL, returns its FULL content (the whole page;
  there is no summary/abstract mode — a page is always read in full) tagged
  with a `fetch_method`: `raw` (non-HTML, or HTML nothing converted) /
  `markitdown` (our HTML→markdown) / `crawl4ai` (headless-browser render) /
  `document` (the URL was a file — PDF/DOCX/XLSX/PPTX/CSV — extracted via
  doc_convert) / `image` (the URL was an image, described by a vision model) /
  `academic` (academic landing page resolved to its full-text PDF).
  markitdown is tried first; the crawl4ai headless render fires **only**
  when the converted text is near-empty (<30 chars) on an HTML GET — so
  JS-rendered pages get rendered, static pages never pay the browser cost.
  A URL that resolves to a FILE rather than a web page (a direct `…/foo.pdf`
  link, a `.docx`/`.xlsx`/`.pptx`/`.csv`, or an image) is ingested like an
  uploaded file — its text is extracted (or the image described) instead of
  the raw bytes being returned. Academic landing pages (arxiv,
  bioRxiv/medRxiv, PubMed Central) are auto-resolved to their full-text PDF —
  just pass the abstract URL.
  The chat view shows the method as a colored badge.
- `exa_search(query, num_results?)` — semantic web search (Exa cloud, API
  key). **Search-only**: returns title + link, no page content. After a
  search, `web_fetch` the most relevant URLs (up to 5, in parallel) and answer
  from the full page text — never from titles/URLs alone.
- `searxng_search(query, num_results?)` — self-hosted SearXNG
  search (no API key). Returns a ranked list of `title` + `link` + `score`
  ONLY — **no snippets** to the model (v9.99.2: snippets were biasing the
  model's fetch choice toward whoever had a tempting blurb instead of the
  source that best answers the intent). The model must then `web_fetch` the
  top URLs (up to 5, in parallel) and answer from page text — never from
  titles — preferring primary/authoritative pages over outlets that merely
  mention the topic. An `infobox` is still surfaced when available. Always
  searches the broad `general` category (v9.124.0: the `news` category param
  was dropped — `general` already returns news outlets AND the authoritative
  source pages, while `news` buried the authoritative page and added noise on
  non-news queries).
  The human Websuche curation panel still shows ~300-char snippets (server
  passes `include_snippets=True` on that path). This is a **standalone
  tool**, not an exa_search backend. Default-disabled at the global gate —
  admin enables it in Settings → Tools.
- `gmail_inbox` / `gmail_read(id)` / `gmail_search(q)` / `gmail_send` /
  `gmail_reply` — requires `gmail.json` configured

## Code execution

- `python_exec(code, timeout?)` — subprocess (`sys.executable`).
  Working dir = session's artifact folder. State persists across calls
  within a session. Files written auto-register as artifacts.

## Delegation / workers

- `delegate_task(agent, prompt)` — fire-and-forget subagent
- `task_status(task_id)` / `task_cancel(task_id)`
- `worker_status(id)` / `worker_abort` / `worker_pause` / `worker_resume` /
  `worker_send(id, msg)` / `worker_ask_user(id, q)`
- `get_artifact_detail(id)` — artifact metadata

## Background tasks (group `background`)

- `run_background_task(title, prompt, group_id?, follow_up?)` — spin off a long,
  output-heavy run as a DETACHED background task (same agent, same model/tools as
  the chat). Returns immediately with a `task_id`; the spawning turn ends — it
  does NOT block. When it finishes, the server **auto-delivers** the result into
  the chat (an auto-fired turn if the chat is idle; otherwise it rides the next
  user turn), so just acknowledge it's started and stop. Differs from
  `delegate_task` (which targets ANOTHER agent and can wait for the result). The
  user sees/controls it in the "Hintergrundaufgaben" panel (live progress, Stopp,
  Transkript). Use only for genuinely long work; quick lookups stay inline.
  **Fan-out (parallel):** for a request with several INDEPENDENT subjects, make
  one call per subject sharing the SAME `group_id`, and put the recombine step
  (compare/summarise/recommend) in `follow_up`. The parts run concurrently and
  the whole group is delivered back in ONE join turn that carries out `follow_up`
  — do NOT create a separate summary task. Calls made in the same turn are
  grouped automatically even without an explicit `group_id`. A background task
  may NOT itself start background tasks (no nesting).

## Scheduler (admin-side from chat)

- `schedule_list()` — every visible schedule (read-only)
- `schedule_history(name?, limit?)` — past runs

(For create/edit/delete from a chat, hit the HTTP API — see `01-api.md`
"Scheduler" + `04-recipes.md`.)

## Code graph

- `code_graph_build(root)` — index a repo
- `code_graph_query(name)` — find a symbol
- `code_graph_impact(symbol)` — callers, dependents
- `code_graph_enhance(symbol)` — pull docstring/summary

## Git / GitHub

- `git_command(cmd, cwd?)` — subset of git verbs
- `github_command(...)` — `gh` CLI passthrough

## MCP

- `mcp_servers()` — list connected MCP servers
- `mcp_connect(spec)` / `mcp_disconnect(name)` — manage

## Skills

- `use_skill(skill="<slug>")` — load full SKILL.md body into context.
  This is how you load THIS skill; load others the same way.

## Discovery

- `tool_search(query)` — find deferred tools. Returns name + schema for
  matching tools so the LLM can invoke them in the next round.

## Helpdesk (Brainy-only)

Only available when `purpose='helpdesk'` (the Brainy bubble). Not in normal
chat. No args — they read scope from the request context (current user +
session).

- `helpdesk_session_info()` — facts about the chat session Brainy was
  opened from (model, project, message count, …).
- `helpdesk_user_context()` — the caller's profile / preferences.
- `helpdesk_user_activity()` — the caller's recent chats / schedules /
  usage.

Brainy's full fixed read-only set (`_HELPDESK_TOOLS`): `use_skill`, the
three `helpdesk_*` tools, `mempalace_query`, `read_document`, `read_file`,
`list_directory`, `search_files`, `context_search`, `context_detail`,
`context_recall`, `web_fetch`, `exa_search`, `searxng_search`. Every
write/exec tool is deliberately excluded.

## User interaction

- `ask_user(question)` — pause turn, wait for user reply (blocks via
  `/v1/chat/answer`)
- `ask_user_for_file(prompt)` — same, file upload
- `ask_llm(prompt, model?)` — sub-LLM call (workflow building block)

## Translation

- `translate_text(text, target, source?, glossary?)`
- `translate_document(path, target, …)`
- `detect_language(text)`
- `list_glossaries()` / `get_glossary(slug)`
- `transcribe_audio(path)` — Whisper/Voxtral
- `generate_audio_overview(topic?, audience?, length?)` — NotebookLM-style **audio
  overview / podcast**. Generates a two-host (Oliver & Jane) English conversation
  voiced via TTS into a `.mp3` (+ a `.md` dialogue script) in the session artifact
  folder. **Source depends on context:** in a PROJECT it discusses the project's
  sources; OUTSIDE a project it discusses the CURRENT CHAT's conversation (so any
  chat can become a podcast). **Multilingual:** the material's language is
  auto-detected and the podcast is spoken in it (Voxtral's 9 languages:
  en/fr/de/es/nl/pt/it/hi/ar), using a voice tagged for that language if one
  exists (else the English default voices — clone a native voice in Settings →
  Tools to upgrade). `length` ∈ short|std|long. (group: `audio`)

## Image / media

- `generate_image(prompt, size?, ...)` — text-to-image for PHOTOS/ILLUSTRATIONS
  only. NOT for diagrams/charts/org charts/flowcharts/timelines — a diffusion
  model can't render legible exact text (labels come out as garbled glyphs). Any
  diagram/chart — even when the user asks for it "as PNG" or "as an image file" —
  is `render_diagram`, NOT generate_image. (The prompt classifier has a dedicated
  `diagram` tool word → the `documents` group, so such requests route to
  render_diagram automatically.)
- `render_diagram(code, format?, title?, theme?, background?)` — render a Mermaid
  diagram to a real SVG/PNG/PDF **artifact** (via mermaid-cli, exact legible
  text). For org charts/flowcharts/structure/timeline/sequence/ER/gantt/etc.
  Returns `path` + `embed` snippets. **For a chat-only diagram**, just write an
  inline ` ```mermaid ` block (rendered live, no tool). **For a report/
  presentation**: `render_diagram` → then embed the file via `write_document`
  `![title](file.png)`. Default format is **PNG** (high-DPI, scale 4 / width
  2000) and embeds in PDF, DOCX AND HTML — take the default for reports. SVG is
  available (`format=svg`) but embeds in HTML ONLY (the PDF/DOCX writers cannot
  place an SVG → emit a "render as PNG" placeholder). write_document embeds
  `![](file)` PNG/JPG images as real pictures in docx/pptx/pdf. **Brand styling:**
  diagrams automatically take the doc-style preset's brand colors + font (node
  fills/borders/edges/pie palette derived from `colors.accent`/`colors.heading`,
  font from `fonts.body`) so they match the report — even when no `style=` is
  passed (the default preset resolves like write_document). Pass an explicit
  `theme=` (default/dark/forest/neutral) to use a generic Mermaid theme instead,
  or `style=""` to opt out of brand colors.

## Nodes (distributed compute)

- `list_nodes()` — peer nodes available

## Tool group → name map (groups in `agent.json → tool_groups`)

```
core          read_file write_file edit_file list_directory search_files
              execute_command tool_search ask_user
documents     read_document write_document edit_document render_diagram
memory        mempalace_query save_chat_to_memory
              mempalace_kg_query mempalace_kg_search mempalace_kg_neighbors
wiki          wiki_write wiki_read wiki_delete wiki_structure
context       context_search context_detail context_recall
web           web_fetch exa_search searxng_search
email         gmail_inbox gmail_read gmail_search gmail_send gmail_reply
delegation    delegate_task task_status task_cancel
background     run_background_task
code_graph    code_graph_build code_graph_query code_graph_impact
              code_graph_enhance
git           git_command github_command
scheduler     schedule_list schedule_history
mcp           mcp_connect mcp_disconnect mcp_servers
skills        use_skill
nodes         list_nodes
code_exec     python_exec
audio         transcribe_audio generate_audio_overview
translation   translate_text translate_document detect_language
              list_glossaries get_glossary
workflows     ask_user_for_file ask_llm
workers       get_artifact_detail worker_status worker_abort worker_pause
              worker_resume worker_send worker_ask_user
image_gen     generate_image
```

Default-enabled groups: `core, memory, context, web, delegation, git,
skills, nodes, scheduler, mcp, workers, translation`.
