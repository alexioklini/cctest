# Agent Tool Reference

Every tool the LLM can call in a chat turn. Names match the actual
`tool_use` block. Dispatch path: sidecar emits `tool_use` → POSTs
`/v1/tools/call` to Brain → `server_lib/tool_mcp.handle_tools_call`
dispatches via `engine.TOOL_DISPATCH` (or MCP fallback) → result returned.

Tools are gated per-call by a 3-layer resolver:
1. Global enable/deferred/purposes (admin-edited `config.json → tool_settings`).
2. Per-agent override (`agent.json → token_config.tool_overrides.<name>`).
3. Call purpose: `interactive | transform | memory_summary |
   research_minimal | helpdesk`.

Brainy (the helpdesk bot) runs with `purpose='helpdesk'` and a fixed
read-only tool set — see "Helpdesk tools" below. Since 9.22.0, the
resolved tool names are also enforced at dispatch: `tool_mcp` rejects any
`tool_use` not in the turn's allowed list before it runs.

Deferred tools are hidden from the initial list and surfaced via `tool_search`.

## Core file ops

- `read_file(path, start_line?, end_line?)` — read text file, optional line range
- `write_file(path, content, mode?)` — create/overwrite; relative paths land
  in the session's artifact folder
- `edit_file(path, old_string, new_string, replace_all?)` — exact-string edit
- `list_directory(path, recursive?)` — ls
- `search_files(root, pattern, ...)` — grep / find
- `execute_command(cmd, cwd?, timeout?)` — shell. NO TTY, no stdin,
  `TERM=dumb`. Banned commands (sudo, rm -rf /, …) rejected.

## Document ops (binary-friendly)

- `read_document(path, ...)` — auto-routes by extension: PDF→markdown,
  docx/pptx/xlsx, images, audio. Honors `.md` companion in
  `<dir>/.brain-extracted/<name>.<ext>.md`. Use this for any non-`.txt`
  attachment.
- `write_document(path, content, format)` — produce docx/pdf/pptx
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

## Context manager

- `context_search(query)` — search the LCM DAG
- `context_detail(node_id)` — one node's content + lineage
- `context_recall(query)` — natural-language recall

## Web / email

- `web_fetch(url)` — GET one URL, returns its content tagged with a
  `fetch_method`: `raw` (non-HTML, or HTML nothing converted) /
  `markitdown` (our HTML→markdown) / `crawl4ai` (headless-browser render) /
  `academic` (academic landing page resolved to its full-text PDF), plus a
  `+abstract` suffix in abstract mode.
  markitdown is tried first; the crawl4ai headless render fires **only**
  when the converted text is near-empty (<30 chars) on an HTML GET — so
  JS-rendered pages get rendered, static pages never pay the browser cost.
  Academic landing pages (arxiv, bioRxiv/medRxiv, PubMed Central) are
  auto-resolved to their full-text PDF and extracted via doc_convert — just
  pass the abstract URL. `mode` arg: `full` (default, whole page) or `abstract`
  (~1500-char survey — the page's own meta-description or its lead **prose**,
  skipping nav/table-of-contents/infobox chrome — for cheap relevance triage
  before fetching the chosen ones in full).
  The chat view shows the method as a colored badge.
- `exa_search(query, num_results?)` — semantic web search (Exa cloud, API
  key). **Search-only**: returns title + link, no page content. Recommended
  flow (set in the configurable tool description): after a search, `web_fetch`
  each URL in `mode="abstract"` first to triage relevance cheaply, then
  `web_fetch(mode="full")` ONLY the results whose abstract shows they help —
  skip full-reading off-topic pages. Never answer from titles/URLs alone.
- `searxng_search(query, num_results?, category?)` — self-hosted SearXNG
  search (no API key). Returns `score` + ~300-char `snippet` per result,
  plus an `infobox` when available. `category` accepts `news`. This is a
  **standalone tool**, not an exa_search backend. Default-disabled at the
  global gate — admin enables it in Settings → Tools.
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

## Image / media

- `generate_image(prompt, size?, ...)` — text-to-image

## Nodes (distributed compute)

- `list_nodes()` — peer nodes available

## Tool group → name map (groups in `agent.json → tool_groups`)

```
core          read_file write_file edit_file list_directory search_files
              execute_command tool_search ask_user
documents     read_document write_document edit_document
memory        mempalace_query save_chat_to_memory
              mempalace_kg_query mempalace_kg_search mempalace_kg_neighbors
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
audio         transcribe_audio
translation   translate_text translate_document detect_language
              list_glossaries get_glossary
workflows     ask_user_for_file ask_llm
workers       get_artifact_detail worker_status worker_abort worker_pause
              worker_resume worker_send worker_ask_user
image_gen     generate_image
```

Default-enabled groups: `core, memory, context, web, delegation, git,
skills, nodes, scheduler, mcp, workers, translation`.
